"""#266: a spin-off and a symbol change of a held multi-day position on the live path, on the simulated venue of
the #206 tests (written before the code). Alpaca credits the spun shares (or renames the position) before the open;
at the session start the engine adopts the change from the venue's positions and a sourced corporate-actions
record (never a guess): the ledger matches the broker, the account is clean, the pot keeps the spun value, and
the spun shares are sold at the next closing auction. Without the record, nothing is adopted (fail closed)."""

from __future__ import annotations

from test_failsafe_drills import DAY1, DAY2, MD, QTY
from test_live_daily_engine import DailyAccount

from kaibot.engine import live_daily as ld
from kaibot.engine.corp_guard import RecentActions


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
    a.ticks(a.open2, 0, 2)  # the session start, then 09:31: the send step
    b = a.eng.ledger.book(a.rid)
    cost = QTY * a.b_px
    assert b.sleeves[(MD, "B")].qty == QTY and abs(b.sleeves[(MD, "B")].cost - 0.75 * cost) < 1e-6
    n = b.sleeves[(MD, "BN")]
    assert n.qty == 20 and abs(n.cost - 0.25 * cost) < 1e-6 and n.origin == "spin_off:B"
    assert a.eng.account_clean and a.eng.recon.last.diffs == [] and a.eng.entry_block() is None
    (sp,) = [x for *_, x in a.eng.journal.entries(kinds=("spinoff",))]
    assert sp["new_symbol"] == "BN" and sp["ref"] == "alpaca-ca:s1"
    assert "spin_off_adopted" in _kinds(a)
    sells = [o for o in a.cls_orders() if o["symbol"] == "BN" and o["side"] == "sell"]
    assert len(sells) == 1 and float(sells[0]["qty"]) == 20  # the spun shares go at DAY2's closing auction
    assert not [o for o in a.cls_orders() if o["symbol"] == "B" and o["side"] == "sell"]  # the parent is kept
    a.ticks(a.open2, 2, 30)
    assert a.eng.kill is None  # no drift halt
    assert len([x for *_, x in a.eng.journal.entries(kinds=("spinoff",))]) == 1  # booked once


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
    a.broker.pos["B"]["qty"] = QTY * 2  # a 2-for-1 split applied late, with Alpaca's SSP row
    a.broker.non_trade.append({"activity_type": "SSP", "id": "ssp1", "date": DAY2, "symbol": "B", "qty": str(QTY)})
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
