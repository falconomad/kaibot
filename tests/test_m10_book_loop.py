"""M10 1.19 (#444): the multi-day book has its own loop (engine/book_loop.py); the intraday path is built only when
an intraday rule is enabled (engine/intraday_legacy.py).

The hard requirement of the ticket: for the multi-day rules nothing changes. The sessions of the live-daily drills
(test_live_daily_engine.DailyAccount: a decision after DAY1's close, the near-close send on DAY2, the fill, the
close; then a full flatten) are run three ways and compared entry for entry:

    old     the engine as it was: the intraday path with the dip rule on (and no candidate), LiveMarket
    loop    the book's loop on the same LiveMarket
    live    the book's loop on BookMarket (the holdings' bars only), as the live process builds it

The whole journal (every entry, in order: decisions, sends, order intents and states, fills, marks, the book's
peak), the venue's orders and positions, every minute row (cash, equity, the sleeves and their marks), the day
results and every event must be the same. The one difference: the shadow rules' end-of-day event is gone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from test_failsafe_drills import DAY1, MD
from test_live_daily_engine import SEND, DailyAccount

from kaibot.engine import live_daily as ld
from kaibot.engine.book_market import BookMarket
from kaibot.engine.core import Engine
from kaibot.engine.journal import Journal
from kaibot.engine.market import LiveMarket
from kaibot.risk.profiles import load_profiles

ROOT = Path(__file__).resolve().parents[1]
NO_INTRADAY = {level: [] for level in load_profiles()}
# The one difference allowed: the shadow rules (intraday, never traded) no longer write their end-of-day event.
SHADOW_EVENTS = ("shadow_session_end",)


class LoopAccount(DailyAccount):
    """The drills' account on the book's loop: `Engine(..., intraday=False)`, no intraday rule at any level."""

    book_market = False

    def engine(self, journal=None) -> Engine:
        if self.book_market:
            market = BookMarket(self.data, ["A", "B", "XLK"], "SPY")
        else:
            market = LiveMarket(self.data, ["A", "B"], {"A": "XLK", "B": "XLK"}, "SPY", self.tmp / "hist",
                                lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
        eng = Engine(self.broker, market, self.store, load_profiles(), set(), now=self.clock,
                     journal=journal or Journal(self.jpath), strategies=NO_INTRADAY, intraday=False)
        if self.book_market:
            market.holdings = eng._md_symbols
        eng.daily = ld.for_engine(eng, self.load, rules=[self.rule])
        return eng

    @staticmethod
    def quiet(eng: Engine) -> None:
        """Nothing to silence: the book's loop asks no market for candidates."""


class LiveAccount(LoopAccount):
    book_market = True


def two_sessions(a) -> None:
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 390)
    a.after_close(a.open2)


def record(a) -> dict:
    """Everything that must not move: the journal's order, fill and book entries, the venue's orders and
    positions, every minute row's equity and cash, the day results and the ledger's closes."""
    entries = [[kind, body] for _, _, kind, body in a.eng.journal.entries()]  # the whole journal, in order
    events = [[e["type"], e.get("payload")] for e in a.store.tables["event"] if e["type"] not in SHADOW_EVENTS]
    orders = sorted((str(o.get("client_order_id")), o["symbol"], o["side"], str(o["qty"]), o["type"],
                     o["time_in_force"], o["status"], str(o.get("filled_qty")), str(o.get("filled_avg_price")))
                    for o in a.broker.orders.values())
    minutes = [(str(r["ts"]), r["cash"], r["equity_venue"], r["equity_shadow"], r["positions"],
                r.get("multi_day")) for r in a.store.tables["ledger"]]
    b = a.eng.ledger.book(a.rid)
    return {"entries": json.loads(json.dumps(entries, default=str)), "orders": orders,
            "events": json.loads(json.dumps(events, default=str)),
            "minutes": json.loads(json.dumps(minutes, default=str)),
            "positions": {s: str(p["qty"]) for s, p in a.broker.pos.items()},
            "cash": b.cash,
            "sleeves": {f"{k[0]}|{k[1]}": (sl.qty, sl.avg_cost, sl.mark) for k, sl in b.sleeves.items()},
            "closes": [repr(c) for c in b.closes],
            "days": [(r["session"], r["pnl_venue"], r["pnl_shadow"], r["trades"])
                     for r in a.store.tables["daily_result"]],
            "halts": [(h["kind"], h.get("session")) for h in a.store.tables.get("halt", [])]}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    out = {}
    mp = pytest.MonkeyPatch()
    try:
        for name, cls in (("old", DailyAccount), ("loop", LoopAccount), ("live", LiveAccount)):
            a = cls(tmp_path_factory.mktemp(name), mp)
            two_sessions(a)
            out[name] = (a, record(a))
    finally:
        mp.undo()
    return out


def test_the_book_engine_is_built_without_an_intraday_rule_and_the_intraday_one_with(runs):
    from kaibot.engine.intraday_legacy import IntradayEngine

    assert type(runs["old"][0].eng) is IntradayEngine and runs["old"][0].eng.intraday
    for name in ("loop", "live"):
        assert type(runs[name][0].eng) is Engine and not runs[name][0].eng.intraday
    a = runs["loop"][0]
    # every other way to build one is the engine as it always was: a research run hands its rule to an engine
    # whose levels list none (scripts/spy_engine.py), tests and replays pass no map at all
    for kw in ({}, {"strategies": NO_INTRADAY}, {"strategies": NO_INTRADAY, "intraday": True},
               {"strategies": {"balanced": ["dip_reversal"]}}):
        assert type(Engine(a.broker, a.eng.market, a.store, load_profiles(), set(), **kw)) is IntradayEngine
    for bad in (None, {}, {"balanced": ["dip_reversal"]}, {"balanced": [MD, "dip_reversal"]}):
        with pytest.raises(ValueError, match="multi-day rules only"):  # the book's engine could never run it
            Engine(a.broker, a.eng.market, a.store, load_profiles(), set(), strategies=bad, intraday=False)


@pytest.mark.parametrize("name", ["loop", "live"])
def test_the_sessions_of_the_old_engine_are_the_same_bytes_on_the_books_own_loop(runs, name):
    old, new = runs["old"][1], runs[name][1]
    assert old["entries"], "the comparison needs the decision, the send and the fill"
    assert [k for k, _ in old["entries"]].count("fill") == 1  # the near-close buy of B, booked once
    assert {"md_decision", "md_send", "md_sent", "om_intent", "fill", "mark", "md_book_peak"} <= {
        k for k, _ in old["entries"]}
    for part in ("entries", "orders", "positions", "cash", "sleeves", "closes", "days", "halts", "events"):
        assert new[part] == old[part], part
    assert new["minutes"] == old["minutes"]  # every minute row: equity, cash, the sleeves and their marks


def test_the_send_is_not_behind_any_market_read_on_the_books_loop(tmp_path, monkeypatch):
    """The minute read of the marks fails on every tick of DAY2: the decision's order still goes out at 15:50 and
    fills, the tick never raises, and the heartbeat says the read failed."""
    a = LiveAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)

    def down(now):
        raise TimeoutError("market data: no answer")

    a.eng.market.update = down
    a.ticks(a.open2, 0, SEND + 1)
    (o,) = a.md_orders()
    assert o["symbol"] == "B" and o["side"] == "buy"
    st = a.eng.tick()
    assert "read failed" in st["market_data"] and a.eng.kill is None and a.eng.error_streak == 0


def test_the_books_market_reads_the_holdings_only(tmp_path, monkeypatch):
    a = LiveAccount(tmp_path, monkeypatch)
    asked = []
    real = a.data.bars

    def bars(symbols, start, end, feed, timeframe="1Min", **kw):
        if timeframe == "1Min":
            asked.append((tuple(symbols), start))
        return real(symbols, start, end, feed, timeframe, **kw)

    a.data.bars = bars
    a.ticks(a.open1, 0, 3)
    assert {s for syms, _ in asked for s in syms} == {"SPY"}  # nothing held: the market fund alone, never A
    a.broker.pos["B"] = {"qty": 40, "avg_entry_price": 100.0}  # a sleeve of B, as the order manager books it
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "B", "side": "buy", "qty": 40, "px": 100.0,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-B-20260803-target-1", "session": DAY1})
    asked.clear()
    a.ticks(a.open1, 3, 6)
    assert {s for syms, _ in asked for s in syms} == {"B", "SPY"}
    first = [start for syms, start in asked if syms == ("B",)]
    assert first == [a.open1]  # a new holding's first read starts at the open: its last bar is LiveMarket's
    assert a.eng.market.last_close("B") and a.eng.market.last_close("A") is None


PROGRAM = r"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, {tests!r})
import pytest
from test_m10_book_loop import LiveAccount, two_sessions
from kaibot.engine.core import Engine
NEVER = {never!r}
mp = pytest.MonkeyPatch()
a = LiveAccount(Path(tempfile.mkdtemp(dir={tmp!r})), mp)
two_sessions(a)
assert type(a.eng) is Engine
assert [k for *_, k, _ in a.eng.journal.entries()].count("fill") == 1, "the session traded"
loaded = sorted(m for m in sys.modules if m in NEVER)
assert not loaded, loaded
print("ok")
"""
# The intraday path: its engine, its shadow rules, the agent room, the custom-ticker check and the intraday rules.
# (The test harness itself imports engine/market.py for the other two accounts, so that one is checked below.)
NEVER = ["kaibot.engine.intraday_legacy", "kaibot.engine.shadow", "kaibot.engine.roomflow", "kaibot.engine.tickercheck",
         "kaibot.agents.room", "kaibot.strategies.dip", "kaibot.strategies.spy_momentum", "kaibot.strategies.orb",
         "kaibot.strategies.news_continuation", "kaibot.strategies.noise_band", "kaibot.strategies.shadow_types"]


def test_two_sessions_on_the_books_loop_import_no_intraday_module(tmp_path):
    code = PROGRAM.format(tests=str(ROOT / "tests"), never=NEVER, tmp=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin", "KAIBOT_MULTIDAY": "on"})
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-3000:]


BUILD = r"""
import sys
from pathlib import Path
import kaibot.engine.__main__ as main
from kaibot.engine.config import Settings
from kaibot.news import NewsDesk
NewsDesk.load_window = lambda self: 0  # the one network read of build(): the news window
s = Settings.from_env()
eng = main.build(s)
from kaibot.engine.core import Engine
from kaibot.engine.book_market import BookMarket
assert type(eng) is Engine and not eng.intraday, type(eng)
assert type(eng.market) is BookMarket and eng.roomflow is None and eng.rules_extra is None
assert eng.daily is not None, "the multi-day path is built"
assert eng.strategies == {{lv: ["trend_momentum"] for lv in eng.policies}}, eng.strategies
bad = sorted(m for m in sys.modules if m in {never!r})
assert not bad, bad
print("ok")
"""


def test_the_live_process_imports_no_intraday_module_when_none_is_enabled(tmp_path):
    """The ticket's check. `build()` as `python -m kaibot.engine` runs it, with the repository's own
    config/profiles.yaml (`intraday.enabled: false`) and KAIBOT_MULTIDAY=on: the engine is the book's, its market
    reads the holdings only, and no module of the intraday path was imported: the intraday engine, the shadow
    rules, the room's flow, the custom-ticker check, every intraday rule, the intraday universe's market
    (engine/market.py) and the intraday replay (its simulator, features and pipeline).

    Not in the list, because the multi-day path itself is built on them: `kaibot.broker.sim`, `kaibot.replay.data`
    and `kaibot.replay.history` (engine/daily.py, the one decision core of research and live, imports them) and
    `kaibot.agents.room` (the pre-open review and the daily note import three text helpers from that file; no
    `Room` is built: the build asserts it)."""
    never = [m for m in NEVER if m != "kaibot.agents.room"] + [
        "kaibot.engine.market", "kaibot.replay.sim", "kaibot.replay.features", "kaibot.replay.pipeline",
        "kaibot.replay.events"]
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin", "KAIBOT_MULTIDAY": "on",
           "KAIBOT_DATA_DIR": str(tmp_path), "ALPACA_API_KEY": "k", "ALPACA_API_SECRET": "s",
           "ALPACA_SECRET": "s", "FRED_API_KEY": "f",
           "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_SERVICE_KEY": "x", "KAIBOT_NEWS_POLL": "off"}
    r = subprocess.run([sys.executable, "-c", BUILD.format(never=never)], capture_output=True, text=True,
                       timeout=300, env=env)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-3000:]


def test_an_open_intraday_trade_is_not_managed_and_stops_every_buy(tmp_path, monkeypatch, caplog):
    """The intraday path was switched off while it held something: the book's engine does not manage that
    position, finds its shares at the venue as unknown ones and opens nothing (fail closed)."""
    a = LoopAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 3)
    a.broker.pos["A"] = {"qty": 5, "avg_entry_price": 100.0}
    a.store.insert("trade", {"run_id": a.rid, "symbol": "A", "qty": 5, "strategy": "dip_reversal",
                             "entry_ts": a.clock().isoformat(), "features": {}})
    eng = a.restart()
    assert not eng.pos and not eng.account_clean and eng.entry_block() == "account not clean"
    assert "open intraday trades in the store (A)" in caplog.text
    assert a.store.tables["event"][-2]["type"] == "account_not_clean" or any(
        e["type"] == "account_not_clean" and e["payload"]["foreign_positions"] == ["A"]
        for e in a.store.tables["event"])


def test_a_full_flatten_on_the_books_loop_sells_through_the_order_manager(tmp_path, monkeypatch):
    """"Flatten and stop" on the book's loop: the sleeve is sold by the order manager under its own client id,
    the fill is booked once and the run ends when the book is flat."""
    a = LiveAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, SEND + 5)
    qty = a.eng.ledger.book(a.rid).sleeves[(MD, "B")].qty
    assert qty > 0
    a.store.insert("command", {"kind": "flatten_and_stop", "payload": {}, "idempotency_key": "k2",
                               "applied_at": None})
    a.eng.apply_commands()
    sale = [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"]
    assert len(sale) == 1 and "-flatten-" in sale[0]["client_order_id"] and float(sale[0]["qty"]) == qty
    assert a.eng.run is not None and a.eng.entry_block() == "flatten and stop pending"
    a.ticks(a.open2, SEND + 5, SEND + 7)
    a.eng.apply_commands()
    assert a.eng.run is None and "B" not in a.broker.pos
    sold = [b for *_, k, b in a.eng.journal.entries() if k == "fill" and b["side"] == "sell"]
    assert len(sold) == 1 and sold[0]["qty"] == qty
