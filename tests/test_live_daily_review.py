"""QA-Ops second-key review of #207. (1) A halt or a full flatten while the live daily path has closing-auction orders
resting at the venue. Before the fix a resting `cls` buy survived "Flatten and stop" (and a run-drawdown end, a new
run, the daily stop) and opened a position for a run that had ended; a resting `cls` sale would fill after the
flatten's own sale. Now the engine cancels the daily path's working orders first (buys on the daily stop, buys and
sales on a full flatten), journals the instruction (`md_cancel`) and never cancels a protective stop. (2) The Docker
engine gets the daily store.
"""

from __future__ import annotations

from dataclasses import dataclass

from test_failsafe_drills import MD
from test_live_daily_engine import DailyAccount

from kaibot.strategies import momentum as mom
from kaibot.strategies.base import TargetList


def _cmd(a, kind):
    a.store.insert("command", {"kind": kind, "payload": {}, "idempotency_key": f"qa-{kind}", "applied_at": None})
    a.eng.apply_commands()


def _resting_buy(tmp_path, monkeypatch, minute=120):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, minute)
    (o,) = a.cls_orders()
    assert o["side"] == "buy" and o["status"] == "new"
    return a, o


def test_flatten_and_stop_cancels_the_resting_cls_buy(tmp_path, monkeypatch):
    a, o = _resting_buy(tmp_path, monkeypatch)
    _cmd(a, "flatten_and_stop")
    assert a.eng.run is None
    assert o["status"] == "canceled"
    a.ticks(a.open2, 120, 390, catch=True)
    a.after_close(a.open2)
    assert "B" not in a.broker.pos or float(a.broker.pos["B"]["qty"]) == 0  # nothing bought for an ended run
    assert not [x for *_, x in a.eng.journal.entries(kinds=("fill",)) if x.get("symbol") == "B"]
    (c,) = [b for *_, b in a.eng.journal.entries(kinds=("md_cancel",))]
    assert c["sides"] == ["buy", "sell"] and c["reason"] == "manual" and c["run_id"] == a.rid
    assert "multi_day_canceled" in [e["type"] for e in a.store.tables["event"]]


def test_the_daily_stop_cancels_the_resting_buy_and_nothing_resends_it(tmp_path, monkeypatch):
    a, o = _resting_buy(tmp_path, monkeypatch)
    a.eng.flatten_all("daily_stop")
    a.eng.day.halted = True
    assert o["status"] == "canceled"
    a.ticks(a.open2, 120, 390, catch=True)
    a.after_close(a.open2)
    assert len(a.cls_orders()) == 1  # not sent again while entries are blocked
    assert "B" not in a.broker.pos or float(a.broker.pos["B"]["qty"]) == 0


def test_the_close_minus_5_flatten_still_leaves_the_resting_buy(tmp_path, monkeypatch):
    a, o = _resting_buy(tmp_path, monkeypatch)
    a.eng.flatten_all("time")
    assert o["status"] == "new"
    assert not [b for *_, b in a.eng.journal.entries(kinds=("md_cancel",))]


def test_a_restart_after_the_halt_keeps_the_cancel(tmp_path, monkeypatch):
    """The instruction is journalled: an engine started again the same day cancels what it recovers."""
    a, o = _resting_buy(tmp_path, monkeypatch)
    a.eng.daily.journal.append("md_cancel", {"run_id": a.rid, "day": "2026-08-04", "sides": ["buy"], "reason": "t"})
    a.restart()
    assert o["status"] == "canceled"


@dataclass(frozen=True)
class SellsB(mom.TrendMomentum):
    def decision_day(self, prev, fill) -> bool:  # #206: the daily path asks a rule only on its decision days
        return True

    def on_event(self, ctx):
        return TargetList(self.name, ctx.fill_session, {}, features={"keep": [], "buy": [], "sell": {"B": "exit"}},
                          resize=False)


def test_a_full_flatten_with_a_resting_cls_sale_never_goes_short(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.rule = type(a.rule)(SellsB(), a.rule.builder)
    a.hold_b()
    a.restart()  # the engine adopts the sleeve's protective stop at start-up (and releases it before the sale)
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 120)
    sells = [x for x in a.cls_orders() if x["side"] == "sell"]
    assert sells and sells[0]["status"] == "new"
    _cmd(a, "flatten_and_stop")
    a.ticks(a.open2, 120, 390, catch=True)
    a.after_close(a.open2)
    q = float(a.broker.pos.get("B", {}).get("qty", 0))
    assert q >= 0, f"short {q}"
    assert q == 0
    assert all(x["status"] != "filled" for x in a.cls_orders() if x["side"] == "sell")
    s = a.eng.ledger.book(a.rid).sleeves.get((MD, "B"))
    assert s is None or s.qty == 0  # the flatten's sale booked once, nothing else


def test_the_docker_engine_gets_the_daily_store_read_only():
    """The image has no data/ folder (.dockerignore): run_engine.sh mounts the daily store into the engine container
    read-only and points KAIBOT_DAILY_DATA at it, or the daily path would never find a store under Docker."""
    from pathlib import Path

    sh = (Path(__file__).resolve().parents[1] / "scripts" / "run_engine.sh").read_text()
    run = sh[sh.index("awake docker run"):].split("\n")[0]
    assert "DAILY_MOUNT" in run
    assert '-v "$DAILY_HOST/daily:/kaibot-daily/daily:ro" -e KAIBOT_DAILY_DATA=/kaibot-daily' in sh
