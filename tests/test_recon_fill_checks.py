"""M9 ticket 1.13 (#438, audit D2): the live reconciliation gets the order manager, so the fill checks
(`foreign_fill`: a fill on an order the engine does not know; `unbooked`: an order with more filled shares than
booked activities) run live; and a replay of normal sessions shows they do not block trading without a cause."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from test_engine import fake_candidate
from test_live_daily_engine import DailyAccount

from kaibot.broker.protocol import OrderRequest
from kaibot.engine import live_daily as ld
from kaibot.engine.reconcile import compare

FILL_KINDS = {"foreign_fill", "unbooked"}


def _kinds(eng) -> set[str]:
    return {d.kind for d in eng.recon.last.diffs}


def _manual_round_trip(a, k: int, sym: str = "A", qty: int = 3) -> None:
    """Someone trades the paper account by hand: buys and sells `qty` shares within two minutes. The position is
    flat again and the cash change is inside the cash tolerance: only the fills say it happened."""
    a.ticks(a.open1, k, k + 1)
    a.broker.submit(OrderRequest(sym, "buy", qty, client_order_id="by-hand-1"))
    a.ticks(a.open1, k + 1, k + 2)
    a.broker.submit(OrderRequest(sym, "sell", qty, client_order_id="by-hand-2"))
    a.ticks(a.open1, k + 2, k + 4)
    assert sym not in a.broker.positions()


def test_a_fill_on_an_order_the_engine_does_not_know_surfaces_as_a_foreign_fill(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    assert a.eng.recon.check("test").ok
    _manual_round_trip(a, 30)
    a.eng._startup_reconcile("recheck")
    got = [(d.kind, d.symbol, d.detail["side"], float(d.detail["qty"])) for d in a.eng.recon.last.diffs]
    assert got == [("foreign_fill", "A", "buy", 3.0), ("foreign_fill", "A", "sell", 3.0)]
    # buys in that stock wait; sales, and buys in other stocks, go out
    assert a.eng.recon.allows("A", "buy") == "unresolved foreign_fill difference in A"
    assert a.eng.recon.allows("A", "sell", qty=1) is None and a.eng.recon.allows("B", "buy") is None
    ev = [e for e in a.store.tables["event"] if e["type"] == "reconcile_blocked"]
    assert ev and {d["kind"] for d in ev[-1]["payload"]["diffs"]} == {"foreign_fill"}
    # it is shown, cannot be accepted (no offset), and clears when the fills are older than FOREIGN_MAX_AGE
    assert {d["offset"] for d in a.eng._recon_status()["diffs"]} == {None}
    a.clock.t += ld.FOREIGN_MAX_AGE + timedelta(minutes=1)
    a.eng._startup_reconcile("recheck")
    assert a.eng.recon.last.ok


def test_the_engines_own_intraday_trade_is_never_a_foreign_fill_also_after_a_restart(tmp_path, monkeypatch):
    """The account's activities hold the intraday path's fills too, which the order manager does not know. Its
    order ids (entry, legs, exit) are remembered in the journal when the trade closes."""
    a = DailyAccount(tmp_path, monkeypatch)
    fake_candidate(a.eng, "A", at=(54,))
    a.ticks(a.open1, 0, 390)
    trades = a.store.tables["trade"]
    assert len(trades) == 1 and trades[0]["exit_ts"] is not None, "an intraday trade in A opened and closed"
    assert [f for f in a.broker.fills if f["symbol"] == "A"]
    a.eng._startup_reconcile("recheck")
    assert a.eng.recon.last.ok
    a.after_close(a.open1)
    eng = a.restart()
    assert eng.recon.last.ok, eng.recon.last.summary()
    a.ticks(a.open2, 0, 5)
    assert a.eng.recon.last.ok and not FILL_KINDS & _kinds(a.eng)


def test_fills_from_before_the_checks_began_on_this_journal_are_not_judged(tmp_path, monkeypatch):
    """At the release that switches the checks on, and on a new journal after a takeover, the account's recent
    activities hold fills of orders this journal never saw. The first look records the time; only fills after it
    are judged (the share and cash checks cover what came before, as they always did)."""
    a = DailyAccount(tmp_path, monkeypatch)
    a.eng.daily.recon_view = lambda: None  # an engine from before this change
    _manual_round_trip(a, 30)
    eng = a.restart()  # the new release starts on the same journal
    assert eng.recon.last.ok
    (since,) = [b for *_, b in eng.journal.entries(kinds=("recon_fills_since",))]
    assert pd.Timestamp(since["ts"]) == pd.Timestamp(a.clock())
    a.ticks(a.open1, 34, 36)
    a.broker.submit(OrderRequest("A", "buy", 2, client_order_id="by-hand-3"))
    a.ticks(a.open1, 36, 38)
    a.eng._startup_reconcile("recheck")
    assert [(d.kind, d.symbol, float(d.detail["qty"])) for d in a.eng.recon.last.diffs
            if d.kind == "foreign_fill"] == [("foreign_fill", "A", 2.0)], "a fill after it is judged"
    assert len(a.eng.journal.entries(kinds=("recon_fills_since",))) == 1


def _normal_sessions(tmp_path, monkeypatch, with_om: bool):
    """Two ordinary sessions: an intraday trade in A on day 1 (opened, closed), the multi-day rule decides after
    the close, a restart overnight, the near-close buy of B on day 2 and its fill. The reconciliation is run at
    every tick (far more often than live: start-up, session start and rechecks only)."""
    a = DailyAccount(tmp_path, monkeypatch)
    seen: list = []

    def run(open_utc, k0, k1):
        for k in range(k0, k1):
            a.ticks(open_utc, k, k + 1)
            if not with_om:
                a.eng.daily.recon_view = lambda: None
            r = compare(**a.eng._recon_inputs(), accepted=a.eng.recon.accepted)
            seen.extend((str(a.clock()), d.kind, d.symbol, d.detail) for d in r.diffs)
            assert a.eng.recon.last.ok and a.eng._may_enter(k) in (True, False)

    fake_candidate(a.eng, "A", at=(54,))
    run(a.open1, 0, 390)
    a.after_close(a.open1)
    a.restart()
    run(a.open2, 0, 390)
    a.after_close(a.open2)
    orders = sorted((o["symbol"], o["side"], float(o["qty"]), o["type"], o["status"],
                     str(o.get("client_order_id"))[:6]) for o in a.broker.orders.values())
    orders += [(f["symbol"], f["side"], float(f["qty"]), "fill", str(f["price"]), "") for f in a.broker.fills]
    return a, seen, orders


def test_a_replay_of_normal_sessions_raises_no_fill_difference_and_sends_the_same_orders(tmp_path, monkeypatch):
    a, seen, orders = _normal_sessions(tmp_path / "with", monkeypatch, with_om=True)
    assert len(a.store.tables["trade"]) == 1 and a.md_orders()[0]["status"] == "filled"  # both paths traded
    assert [s for s in seen if s[1] in FILL_KINDS] == [], "a fill check fired in a normal session"
    assert not [e for e in a.store.tables["event"] if e["type"] in ("reconcile_blocked", "ledger_mismatch")]
    b, seen_b, orders_b = _normal_sessions(tmp_path / "without", monkeypatch, with_om=False)
    assert orders == orders_b and len(orders) >= 5, "the same orders with and without the fill checks"
    assert seen == seen_b, "and the same differences at every tick (none of the fill kinds, nothing new)"


def test_fill_activities_that_cannot_be_read_count_as_a_failed_check(tmp_path, monkeypatch):
    """The fill checks cannot say "no such fill" without the activities: fail closed, as for a failed positions
    read; the next check that reads them clears it."""
    from kaibot.broker.protocol import BrokerError

    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    real = a.broker.activities

    def down(after=None, types=("FILL",)):
        if "FILL" in types:
            raise BrokerError(503, "activities unavailable")
        return real(after, types=types)

    a.broker.activities = down
    a.eng._startup_reconcile("recheck")
    assert [d.kind for d in a.eng.recon.last.diffs] == ["unavailable"]
    assert a.eng.entry_block() == "ledger and broker differ (reconciliation)"
    a.broker.activities = real
    a.eng._recheck_reconcile(force=True)
    assert a.eng.recon.last.ok and a.eng.entry_block() is None


# ---- QA-Ops review of #522: a fill difference must not stop the rebalance, and the owner can accept it ----
def test_a_foreign_fill_in_one_stock_does_not_stop_the_next_sessions_buys_in_another(tmp_path, monkeypatch):
    """Someone traded A by hand on day 1. Before the fix every buy asked `entry_block`, which said "ledger and
    broker differ" for any open difference: the near-close buy of B never went out, for three days, and nothing
    could be accepted. Now the fill difference stops buys in A only."""
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    _manual_round_trip(a, 30)
    a.ticks(a.open1, 34, 390)
    a.after_close(a.open1)
    eng = a.restart()
    assert _kinds(eng) == {"foreign_fill"} and eng.entry_block() is None
    assert eng.recon.allows("A", "buy") == "unresolved foreign_fill difference in A"
    a.ticks(a.open2, 0, 390)
    assert _kinds(a.eng) == {"foreign_fill"}, "still open: it was not accepted and is younger than three days"
    assert [o["status"] for o in a.md_orders()] == ["filled"], "the buy of B went out and filled"


def test_a_share_or_cash_difference_or_a_failed_read_still_stops_every_buy(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    a.broker.submit(OrderRequest("A", "buy", 2, client_order_id="by-hand-kept"))  # bought by hand and kept
    a.ticks(a.open1, 30, 32)
    a.eng._startup_reconcile("recheck")
    assert {"quantity", "foreign_fill"} <= _kinds(a.eng)
    assert a.eng.entry_block() == "ledger and broker differ (reconciliation)"


def test_the_owners_acceptance_covers_the_foreign_fills_that_were_shown_and_no_later_one(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    a.broker.submit(OrderRequest("A", "buy", 2, client_order_id="by-hand-kept"))
    a.ticks(a.open1, 30, 32)
    a.eng._startup_reconcile("recheck")
    cash = [d for d in a.eng._recon_status()["diffs"] if d["offset"] is not None]
    assert {d["kind"] for d in cash} >= {"quantity"}
    # the dashboard's command: what the screen showed (the acceptable rows); the fill on the unknown order was on
    # that screen too and is accepted with them
    a.eng._accept_reconcile({"reason": "my own test buy", "shown": [
        {"kind": d["kind"], "symbol": d["symbol"], "offset": d["offset"]} for d in cash]})
    assert a.eng.recon.last.ok, a.eng.recon.last.summary()  # (the drift halt for the shares held is its own)
    (res,) = [b for *_, b in a.eng.journal.entries(kinds=("reconcile_resolved",))]
    assert len(res["foreign"]) == 1 and res["offsets"]
    assert {d.kind for d in a.eng.recon.last.accepted} >= {"quantity", "foreign_fill"}
    # a later fill on an unknown order is a new difference: the acceptance named the fill, not the stock
    a.broker.submit(OrderRequest("A", "buy", 1, client_order_id="by-hand-again"))
    a.ticks(a.open1, 32, 34)
    a.eng._startup_reconcile("recheck")
    assert [float(d.detail["qty"]) for d in a.eng.recon.last.diffs if d.kind == "foreign_fill"] == [1.0]


def test_foreign_fills_alone_are_accepted_from_the_command_line_and_stay_accepted_after_a_restart(tmp_path,
                                                                                                  monkeypatch):
    from kaibot.engine import reconcile

    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    _manual_round_trip(a, 30)
    a.eng._startup_reconcile("recheck")
    assert _kinds(a.eng) == {"foreign_fill"}
    # the dashboard's command cannot: it carries only rows with a number, and none is open
    with pytest.raises(ValueError, match="nothing accepted"):
        a.eng._accept_reconcile({"reason": "tap", "shown": [{"kind": "quantity", "symbol": "A", "offset": 3}]})
    assert _kinds(a.eng) == {"foreign_fill"}
    assert reconcile.main(["--accept", "my own round trip in A"], journal=a.eng.journal) == 0
    a.eng._startup_reconcile("recheck")  # the engine folds the acceptance at its next check
    assert a.eng.recon.last.ok and a.eng.recon.allows("A", "buy") is None
    eng = a.restart()
    assert eng.recon.last.ok and len(eng.recon.last.accepted) == 2
    # nothing left to accept: a second acceptance says so
    assert reconcile.main(["--accept", "again"], journal=a.eng.journal) == 2


def test_an_unbooked_fill_stops_buys_in_its_own_stock_only():
    from kaibot.engine.reconcile import Difference, Reconciler, Reconciliation

    r = Reconciler(lambda: {})
    r.checked, r.last = True, Reconciliation([Difference("unbooked", "B", {"coid": "c", "venue": 5, "booked": 2})])
    assert not r.blocks_every_buy()
    assert r.allows("B", "buy") and r.allows("C", "buy") is None and r.allows("B", "sell", qty=1) is None
    r.last = Reconciliation([Difference("foreign_fill", None, {"activity": "x"})])
    assert r.blocks_every_buy(), "a fill that names no stock cannot be scoped: every buy waits"
