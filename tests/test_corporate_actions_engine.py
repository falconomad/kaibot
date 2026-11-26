"""#266: a spin-off and a symbol change of a held multi-day position on the live path, on the simulated venue of
the #206 tests (written before the code). Alpaca credits the spun shares (or renames the position) before the open;
at the session start the engine adopts the change from the venue's positions and a sourced corporate-actions
record (never a guess): the ledger matches the broker, the account is clean, the pot keeps the spun value, and
the spun shares are sold at the next closing auction. Without the record, nothing is adopted (fail closed)."""

from __future__ import annotations

from test_failsafe_drills import DAY1, DAY2, MD, QTY
from test_live_daily_engine import SEND, DailyAccount, WantsB

from kaibot.engine import live_daily as ld
from kaibot.engine.corp_guard import RecentActions
from kaibot.strategies.base import TargetList


class Feed:
    def __init__(self, block: dict | None = None) -> None:
        self.block, self.calls = block or {}, 0

    def __call__(self, path, params):
        self.calls += 1
        return {"corporate_actions": self.block, "next_page_token": None}


SPIN = {"spin_offs": [{"source_symbol": "B", "new_symbol": "BN", "ex_date": DAY2, "new_rate": 1, "source_rate": 2,
                       "id": "s1"}]}
RENAME = {"name_changes": [{"old_symbol": "B", "new_symbol": "B2", "process_date": DAY2, "id": "n1"}]}


class Held(DailyAccount):
    def __init__(self, tmp_path, monkeypatch, feed: Feed | None):
        self.feed = feed
        super().__init__(tmp_path, monkeypatch)

    def engine(self, journal=None):
        eng = super().engine(journal)
        eng.recent_actions = RecentActions(get=self.feed) if self.feed is not None else None
        eng.daily = ld.for_engine(eng, self.load, rules=[self.rule])
        return eng

    def hold(self):
        """40 shares of B held by the multi-day rule since DAY1 (no stop: the rule has none)."""
        self.ticks(self.open1, 0, 30)
        px = float(self.eng.market.last_close("B"))
        self.broker.pos["B"] = {"qty": QTY, "avg_entry_price": px}
        self.eng._book("fill", {"run_id": self.rid, "strategy": MD, "symbol": "B", "side": "buy", "qty": QTY,
                                "px": px, "fees": 0.0, "coid": f"kb{self.rid}-{MD}-B-20260803-target-1",
                                "session": DAY1})
        self.b_px = px
        self.ticks(self.open1, 30, 390)
        self.after_close(self.open1)


def _kinds(a):
    return [e["type"] for e in a.store.tables["event"]]


def test_a_spin_off_is_adopted_at_the_cost_basis_and_sold_at_the_next_close(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(SPIN))
    a.hold()
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)  # Alpaca, before DAY2's open
    a.ticks(a.open2, 0, 2)  # the session start: the adoption
    b = a.eng.ledger.book(a.rid)
    cost = QTY * a.b_px
    assert b.sleeves[(MD, "B")].qty == QTY and abs(b.sleeves[(MD, "B")].cost - 0.75 * cost) < 1e-6
    n = b.sleeves[(MD, "BN")]
    assert n.qty == 20 and abs(n.cost - 0.25 * cost) < 1e-6 and n.origin == "spin_off:B"
    assert a.eng.account_clean and a.eng.recon.last.diffs == [] and a.eng.entry_block() is None
    (sp,) = [x for *_, x in a.eng.journal.entries(kinds=("spinoff",))]
    assert sp["new_symbol"] == "BN" and sp["ref"] == "alpaca-ca:s1"
    assert "spin_off_adopted" in _kinds(a)
    a.ticks(a.open2, 2, SEND + 2)  # 15:50: the send step (issue #333)
    sells = [o for o in a.md_orders() if o["symbol"] == "BN" and o["side"] == "sell"]
    assert len(sells) == 1 and float(sells[0]["qty"]) == 20  # the spun shares go at DAY2's close
    assert not [o for o in a.md_orders() if o["symbol"] == "B" and o["side"] == "sell"]  # the parent is kept
    a.ticks(a.open2, SEND + 2, 390)
    assert a.eng.kill is None  # no drift halt
    assert len([x for *_, x in a.eng.journal.entries(kinds=("spinoff",))]) == 1  # booked once


class SellsB(WantsB):
    """trend_momentum's settings; its decision: sell B (once held)."""

    def on_event(self, ctx):
        if "B" not in ctx.held:
            return super().on_event(ctx)
        return TargetList(self.name, ctx.fill_session, {}, features={"keep": [], "buy": [], "sell": {"B": "rank"}},
                          resize=False)


class WantsBAndZ(WantsB):
    """Its decision: buy B and Z, a name the daily view has no column for."""

    def on_event(self, ctx):
        return TargetList(self.name, ctx.fill_session, {"B": 1.0, "Z": 1.0},
                          features={"keep": [], "buy": ["B", "Z"], "sell": {}}, resize=False)


def _rule(a, strategy):
    a.rule = ld.Rule(strategy, a.rule.builder)
    a.eng.daily = ld.for_engine(a.eng, a.load, rules=[a.rule])
    a.eng.daily.startup()


def test_780_a_spin_off_on_a_decision_day_sells_the_new_shares_and_sends_every_other_order(tmp_path, monkeypatch):
    """#780: the decision of DAY1 sells B; before DAY2's open a spin-off credits BN, which the daily view the send
    sizes with does not have. The whole send used to fail with KeyError('BN') at every pass. Now BN is not sized:
    its 20 shares go out as a full-position sale in DAY2's window (the pre-registrations' "sold at the next closing
    auction", trend_momentum Amendment 7.2, mom_12_1_band 6.2), and so does B's sale."""
    a = Held(tmp_path, monkeypatch, Feed(SPIN))
    _rule(a, SellsB())
    a.hold()
    (dec,) = [x for *_, x in a.eng.journal.entries(kinds=("md_decision",)) if x["fill_session"] == DAY2]
    assert dec["target"]["sell"] == [["B", "rank"]] and dec["target"]["weights"] == []
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.ticks(a.open2, 0, 390)
    sells = sorted((o["symbol"], float(o["qty"]), o["status"]) for o in a.md_orders() if o["side"] == "sell")
    # (this venue has no minute bars of BN, so its sale cannot trade here: sent once, in the window, all 20 shares)
    assert [x[:2] for x in sells] == [("B", QTY), ("BN", 20.0)] and sells[0][2] == "filled"
    assert all(o["client_order_id"].endswith("-20260804-sell-1") for o in a.md_orders() if o["side"] == "sell")
    (send,) = [x for *_, x in a.eng.journal.entries(kinds=("md_send",)) if x["session"] == DAY2]
    assert sorted((t[0], t[1], t[3]) for t in send["targets"]) == [("B", 0, "rank"), ("BN", 0, "spin_off_adopted")]
    assert "not_sent" not in send
    assert [x for *_, x in a.eng.journal.entries(kinds=("md_sent",)) if x["session"] == DAY2]  # the send completed
    assert "multi_day_error" not in _kinds(a)


def test_780_a_buy_the_daily_view_has_no_column_for_is_left_out_and_the_rest_is_bought(tmp_path, monkeypatch):
    """#780: one unknown symbol never blocks the send: the decision wants B and Z, the view has no Z. B is bought;
    Z is journalled as not sent (`no_daily_bar`) and said once."""
    a = Held(tmp_path, monkeypatch, None)
    _rule(a, WantsBAndZ())
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 390)
    assert [(o["symbol"], o["side"], o["status"]) for o in a.md_orders()] == [("B", "buy", "filled")]
    (send,) = [x for *_, x in a.eng.journal.entries(kinds=("md_send",)) if x["session"] == DAY2]
    assert send["not_sent"] == {"Z": "no_daily_bar"} and [t[0] for t in send["targets"]] == ["B"]
    assert "multi_day_error" not in _kinds(a) and _kinds(a).count("multi_day_no_daily_bar") == 1


def test_a_restart_after_the_adoption_books_nothing_again(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(SPIN))
    a.hold()
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.ticks(a.open2, 0, 3)
    a.restart()
    a.ticks(a.open2, 3, 6)
    assert len([x for *_, x in a.eng.journal.entries(kinds=("spinoff",))]) == 1
    assert a.eng.ledger.book(a.rid).sleeves[(MD, "BN")].qty == 20 and a.eng.account_clean


def test_without_a_sourced_record_the_new_shares_are_not_adopted(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed({}))
    a.hold()
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.ticks(a.open2, 0, 2)
    assert (MD, "BN") not in a.eng.ledger.book(a.rid).sleeves
    assert not a.eng.account_clean  # as before: a position the engine does not know blocks entries


def test_a_spin_off_whose_shares_do_not_match_the_ratio_is_not_adopted(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(SPIN))  # the record says 1 per 2: 20 shares
    a.hold()
    a.broker.spin_off("B", "BN", ratio=1.0, basis_share=0.25, day=DAY2)  # the venue credits 40
    a.ticks(a.open2, 0, 2)
    assert (MD, "BN") not in a.eng.ledger.book(a.rid).sleeves
    assert "corporate_action_not_adopted" in _kinds(a)


def test_a_symbol_change_moves_the_sleeve_and_the_account_stays_clean(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(RENAME))
    a.hold()
    a.broker.symbol_change("B", "B2", day=DAY2)
    a.ticks(a.open2, 0, 2)
    b = a.eng.ledger.book(a.rid)
    assert (MD, "B") not in b.sleeves and b.sleeves[(MD, "B2")].qty == QTY
    assert a.eng.account_clean and a.eng.recon.last.diffs == []
    assert not [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"]  # never the old


def test_a_flatten_never_sells_more_than_the_broker_holds(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed({}))
    a.hold()
    a.broker.pos["B"]["qty"] = 10  # the venue holds fewer shares than the ledger (an unbooked reverse split)
    a.ticks(a.open2, 0, 2)
    a.eng.flatten_all("test")
    sells = [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"]
    assert sells and all(float(o["qty"]) <= 10 for o in sells)


def test_a_spin_off_record_without_a_share_ratio_is_not_adopted(tmp_path, monkeypatch):
    """QA-Ops second key (#275): adoption needs the broker's count to match the record. A record without
    `new_rate` / `source_rate` gives nothing to match, so the new shares are not adopted (entries stay blocked)."""
    rec = {"spin_offs": [{k: v for k, v in SPIN["spin_offs"][0].items() if k not in ("new_rate", "source_rate")}]}
    a = Held(tmp_path, monkeypatch, Feed(rec))
    a.hold()
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.ticks(a.open2, 0, 2)
    assert (MD, "BN") not in a.eng.ledger.book(a.rid).sleeves
    assert not list(a.eng.journal.entries(kinds=("spinoff",)))
    (ev,) = [e for e in a.store.tables["event"] if e["type"] == "corporate_action_not_adopted"]
    assert "no share ratio" in str(ev)
    assert not a.eng.account_clean


# ---------------------------------------------------------------- a late credit (after the session start)


def test_a_spin_off_credited_after_the_session_start_is_adopted_at_the_next_tick(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(SPIN))
    a.hold()
    a.ticks(a.open2, 0, 3)  # the session starts before Alpaca has credited anything
    assert (MD, "BN") not in a.eng.ledger.book(a.rid).sleeves
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)  # credited at 09:33
    a.ticks(a.open2, 3, 10)
    assert a.eng.ledger.book(a.rid).sleeves[(MD, "BN")].qty == 20
    assert a.eng.kill is None and a.eng.account_clean and a.eng.recon.last.diffs == []
    assert a.eng.entry_block() is None
    assert len([x for *_, x in a.eng.journal.entries(kinds=("spinoff",))]) == 1


def test_a_split_credited_after_the_session_start_is_booked_at_the_next_tick(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed({}))
    a.hold()
    a.ticks(a.open2, 0, 3)
    a.broker.pos["B"]["qty"] = QTY * 2  # a 2-for-1 split applied late, with Alpaca's SPLIT row
    a.broker.non_trade.append({"activity_type": "SPLIT", "id": "ssp1", "date": DAY2, "symbol": "B", "qty": str(QTY)})
    a.ticks(a.open2, 3, 10)
    assert a.eng.ledger.book(a.rid).sleeves[(MD, "B")].qty == QTY * 2
    assert a.eng.kill is None and a.eng.recon.last.diffs == []


def test_a_late_difference_without_a_record_still_halts_entries(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed({}))
    a.hold()
    a.ticks(a.open2, 0, 3)
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.ticks(a.open2, 3, 10)
    assert (MD, "BN") not in a.eng.ledger.book(a.rid).sleeves and a.eng.kill == "drift"


def test_a_late_adoption_does_not_lift_the_drift_halt_while_another_difference_stays(tmp_path, monkeypatch):
    """QA-Ops (#285): the late spin-off is adopted, but the books still disagree elsewhere (here: a cash difference
    the reconciliation reports). The drift halt stays until they agree."""
    a = Held(tmp_path, monkeypatch, Feed(SPIN))
    a.hold()
    a.ticks(a.open2, 0, 3)
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.eng.kill = "drift"  # the drift check halted entries before the late credit was adopted
    monkeypatch.setattr(a.eng, "_reconciled", lambda: False)
    a.ticks(a.open2, 3, 10)
    assert a.eng.ledger.book(a.rid).sleeves[(MD, "BN")].qty == 20  # adopted
    assert a.eng.kill == "drift"  # but not lifted
    assert not [e for e in a.store.tables["event"] if e["type"] == "halt_cleared"]


# ---------------------------------------------------------------- a cash merger of a held stock

CASH = {"cash_mergers": [{"acquiree_symbol": "B", "acquirer_symbol": "BIG", "rate": 120.0, "effective_date": DAY2,
                          "id": "m1"}]}


def test_a_cash_merger_closes_the_sleeve_at_the_cash_price_and_books_the_cash(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(CASH))
    a.hold()
    a.broker.cash = 10_000.0 - QTY * a.b_px  # a cash book, so the reconciliation compares cash too
    cash0 = a.eng.ledger.book(a.rid).cash
    a.broker.cash_merger("B", price=120.0, day=DAY2)  # Alpaca, before DAY2's open
    a.ticks(a.open2, 0, 6)
    b = a.eng.ledger.book(a.rid)
    assert (MD, "B") not in b.sleeves
    assert abs(b.cash - cash0 - QTY * 120.0) < 1e-6
    (f,) = [x for *_, x in a.eng.journal.entries(kinds=("fill",)) if x["side"] == "sell"]
    assert f["px"] == 120.0 and f["fees"] == 0.0 and f["reason"] == "cash_merger" and f["ref"] == "alpaca-ca:m1"
    assert "cash_merger_booked" in _kinds(a)
    assert a.eng.kill is None and a.eng.account_clean and a.eng.recon.last.diffs == []
    assert not [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"]  # no sale tried
    a.eng.flatten_all("test")  # a later full flatten has nothing of B to sell
    assert not [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"]


def test_a_vanished_position_without_a_merger_record_is_not_booked(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed({}))
    a.hold()
    a.broker.cash_merger("B", price=120.0, day=DAY2)
    a.ticks(a.open2, 0, 3)
    assert (MD, "B") in a.eng.ledger.book(a.rid).sleeves  # never guessed: entries stay blocked, as before


# ---------------------------------------------------------------- the fail-safes on an ex-date (#266 follow-up)
from kaibot.engine import failsafe  # noqa: E402


def _rows(a, session):
    return {(r["strategy"], r["symbol"]): r for r in a.store.tables.get("ledger_position", [])
            if r["run_id"] == a.rid and str(r["session"]) == session}


def test_after_a_split_at_the_open_the_fail_safes_read_the_split_share_count(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed({}))
    a.hold()
    a.broker.pos["B"]["qty"] = QTY * 2  # a 2-for-1 split before DAY2's open, with Alpaca's SPLIT row
    a.broker.non_trade.append({"activity_type": "SPLIT", "id": "ssp1", "date": DAY2, "symbol": "B", "qty": str(QTY)})
    a.ticks(a.open2, 0, 2)
    rows = _rows(a, DAY2)
    assert rows[(MD, "B")]["qty"] == QTY * 2  # written at the session start, not only at the close
    book = failsafe.book_from_store(a.store)
    assert sum(s.qty for s in book.sleeves if s.symbol == "B") == QTY * 2
    p = failsafe.plan(a.broker.positions(), a.broker.open_orders(), book, flatten=True)
    assert not [x for x in p.sell if x[0] == "B"] and "B" not in p.close  # no split share is sold as "intraday"
    assert p.multi_day["B"] == QTY * 2
    pj = failsafe.plan(a.broker.positions(), a.broker.open_orders(), failsafe.book_from_journal(a.eng.journal),
                       flatten=True)
    assert not [x for x in pj.sell if x[0] == "B"] and pj.multi_day["B"] == QTY * 2  # the watchdog's view too


def test_after_a_spin_off_the_fail_safes_count_the_new_shares_as_the_rule_s(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(SPIN))
    a.hold()
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.ticks(a.open2, 0, 2)
    rows = _rows(a, DAY2)
    assert rows[(MD, "BN")]["qty"] == 20 and rows[(MD, "B")]["qty"] == QTY
    p = failsafe.plan(a.broker.positions(), [], failsafe.book_from_store(a.store), flatten=True)
    assert "BN" not in p.close and p.multi_day["BN"] == 20


def test_a_row_written_at_the_open_for_a_sleeve_sold_during_the_day_is_zero_at_the_close(tmp_path, monkeypatch):
    a = Held(tmp_path, monkeypatch, Feed(SPIN))
    a.hold()
    a.broker.spin_off("B", "BN", ratio=0.5, basis_share=0.25, day=DAY2)
    a.ticks(a.open2, 0, 2)
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "BN", "side": "sell", "qty": 20.0, "px": 45.0,
                         "fees": 0.0, "session": DAY2})  # as if the closing auction had sold the spun shares
    a.broker.pos.pop("BN")
    a.ticks(a.open2, 2, 390)
    a.after_close(a.open2)
    rows = _rows(a, DAY2)
    assert rows[(MD, "BN")]["qty"] == 0 and rows[(MD, "B")]["qty"] == QTY


def test_a_flatten_of_two_multi_day_symbols_sells_both(tmp_path, monkeypatch):
    """QA-Ops (#275 on #282): the flatten reads the venue's positions once for every symbol (#275) and #282's sale
    path read them again into the same name; the second symbol then failed (`float` has no `.get`) and the flatten
    stopped. Both sleeves are sold, each once."""
    a = Held(tmp_path, monkeypatch, Feed({}))
    a.hold()
    px = float(a.eng.market.last_close("A") or 100.0)
    a.broker.pos["A"] = {"qty": 10, "avg_entry_price": px}
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "A", "side": "buy", "qty": 10, "px": px,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-A-20260803-target-1", "session": DAY1})
    a.ticks(a.open2, 0, 2)
    failed = {}
    a.eng._flatten_md("book_drawdown", failed, a.eng.monotonic())
    sold = sorted(o["symbol"] for o in a.broker.orders.values() if o["side"] == "sell" and o["type"] == "market")
    assert failed == {} and sold == ["A", "B"]
