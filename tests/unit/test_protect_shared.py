"""Issue #192 (QA-Ops review of #187): two strategies hold the same symbol. Each sleeve keeps its own protective
stop, and the stops of a symbol, with every other open sell order there, never ask for more shares than the venue
position holds: partial fills, re-arms, one sleeve exiting while the other holds, a stale sleeve."""

from __future__ import annotations

import random
from datetime import timedelta

from test_order_manager import DAY, manager, venue

from kaibot.broker.protocol import BrokerError
from kaibot.engine.orders import St
from kaibot.engine.protect import ProtectiveStops

KA, KB = ("rule", "A"), ("trend", "A")
PX = {KA: 90.0, KB: 91.0}


def shared(qty=50, new_id=True, **kw):
    """One venue position of `qty` in A; every stop request is checked against the shares no other order holds."""
    b, clock = venue(replace_new_id=new_id)
    om, _, _ = manager(b, clock)
    b.pos["A"] = {"qty": qty, "avg_entry_price": 100.0}
    asked: list[tuple[str, int, float]] = []  # (what, qty asked, free shares at the venue then)
    real_submit, real_replace = b.submit, b.replace

    def submit(req):
        if req.side == "sell":
            asked.append(("submit", int(req.qty), b._free_shares(req.symbol)))
        return real_submit(req)

    def replace(oid, **k):
        o = b.orders.get(oid)
        if k.get("qty") is not None and o is not None:  # the qty sent is the order's total: what rests is less
            done = float(o.get("filled_qty") or 0)
            left = float(o["qty"]) - done
            asked.append(("replace", int(k["qty"] - done), b._free_shares(o["symbol"]) + left))
        return real_replace(oid, **k)

    b.submit, b.replace = submit, replace
    return b, clock, om, ProtectiveStops(om, run="1", **kw), asked


def resting(om, symbol="A") -> int:
    return int(sum(r.qty - r.cum for r in om.working(symbol=symbol, purposes=("stop",))))


def qty_of(ps, key) -> int:
    return int(sum(r.qty - r.cum for r in ps.stops_of(key)))


def never_over(asked):
    assert all(q <= free + 1e-9 for _, q, free in asked), asked


def test_192_two_sleeves_in_one_symbol_each_get_their_own_stop_and_the_sum_is_the_venue_position():
    b, clock, om, ps, asked = shared(50)
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (30, 20) and resting(om) == 50
    assert ps.stops_of(KA)[0].stop == 90.0 and ps.stops_of(KB)[0].stop == 91.0
    assert ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY) == []  # nothing to do
    never_over(asked)


def test_192_a_stale_sleeve_never_creates_a_stop_on_shares_another_sleeve_already_covers():
    """trend's sale of 20 filled at the venue but is not booked yet: the caller still says trend holds 20."""
    b, clock, om, ps, asked = shared(50)
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    assert ps.release(KB, 0)  # the sale frees the shares: trend's stop is cancelled
    b.pos["A"]["qty"] = 30  # the sale filled
    did = ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    assert ps.stops_of(KB) == [] and qty_of(ps, KA) == 30 and resting(om) <= 30, did
    assert not [a for a in asked[2:] if a[0] == "submit"], asked  # nothing asked for the 20 shares that are gone
    never_over(asked)


def test_192_partial_fills_of_one_sleeve_are_protected_as_they_fill_and_capped_at_the_free_shares():
    b, clock, om, ps, asked = shared(30)
    ps.ensure({KA: 30.0}, PX, clock(), DAY)
    b.pos["A"]["qty"] = 40  # trend's entry: 10 of 20 filled
    ps.ensure({KA: 30.0, KB: 10.0}, PX, clock(), DAY)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (30, 10)
    b.pos["A"]["qty"] = 45  # 5 more filled at the venue, but the caller's sleeve already says 20
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    assert qty_of(ps, KB) == 15 and resting(om) == 45  # capped at what rule's stop leaves free
    b.pos["A"]["qty"] = 50
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (30, 20) and resting(om) == 50
    never_over(asked)


def test_192_a_failed_position_read_creates_or_grows_nothing_in_a_shared_symbol():
    b, clock, om, ps, asked = shared(50)
    ps.ensure({KA: 30.0}, PX, clock(), DAY)
    real = b.positions

    def down():
        raise BrokerError(503, "unavailable")

    b.positions = down
    did = ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    assert ps.stops_of(KB) == [] and any(d.startswith("wait trend A") for d in did), did
    b.positions = real
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    assert qty_of(ps, KB) == 20
    never_over(asked)


def test_192_a_re_arm_in_a_shared_symbol_covers_only_the_shares_the_other_sleeve_leaves_free():
    b, clock, om, ps, asked = shared(50, confirm_polls=3)
    ps.ensure({KA: 30.0}, PX, clock(), DAY)
    clock.set(clock() + timedelta(days=40))
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)  # trend's stop is 40 days younger
    (old,) = ps.stops_of(KA)
    real_cancel = b.cancel

    def cancel(oid):  # 10 shares leave the venue while rule's old stop is being cancelled (not booked yet)
        real_cancel(oid)
        b.pos["A"]["qty"] = 40

    b.cancel = cancel
    clock.set(clock() + timedelta(days=41))  # rule's stop within 10 days of its expiry, trend's not
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), "2026-12-05")
    (new,) = ps.stops_of(KA)
    assert new.coid != old.coid and om.get(old.coid).state is St.CANCELED
    assert int(new.qty) == 20 and qty_of(ps, KB) == 20 and resting(om) == 40
    (j,) = [body for _, _, k, body in om.journal.entries(kinds=("stop_rearm",))]
    assert (j["broker_qty"], j["held_by_others"], j["qty"]) == (40, 20, 20)
    never_over(asked)


def test_192_a_re_arm_whose_old_stop_partly_filled_keeps_the_other_sleeves_stop_and_the_sum_in_bounds():
    b, clock, om, ps, asked = shared(50, confirm_polls=3)
    ps.ensure({KA: 30.0}, PX, clock(), DAY)
    clock.set(clock() + timedelta(days=40))
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)

    def cancel(oid):  # rule's stop sold 10 before the cancel arrived
        o = b.orders[oid]
        o.update(filled_qty=10, status="canceled")
        b.pos["A"]["qty"] = 40

    b.cancel = cancel
    clock.set(clock() + timedelta(days=41))
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), "2026-12-05")
    assert qty_of(ps, KA) == 20 and qty_of(ps, KB) == 20 and resting(om) == 40
    never_over(asked)


def test_192_one_sleeve_exits_while_the_other_holds_and_keeps_its_stop_untouched():
    b, clock, om, ps, asked = shared(50)
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    (ra,) = ps.stops_of(KA)
    assert ps.release(KB, 0)  # trend sells everything
    assert ps.stops_of(KB) == [] and ps.stops_of(KA) == [ra] and int(ra.qty) == 30
    b.pos["A"]["qty"] = 30  # the sale filled and is booked: trend's sleeve is gone
    n = len(b.calls)
    assert ps.ensure({KA: 30.0}, PX, clock(), DAY) == []
    assert b.calls[n:] == [] or not [c for c in b.calls[n:] if "cancel" in c or "replace" in c]
    assert ps.stops_of(KA) == [ra] and om.get(ra.coid).state is St.ACCEPTED
    never_over(asked)


def test_192_a_caller_that_passes_one_strategys_sleeves_can_scope_ensure_to_that_strategy():
    """Each strategy may run its own `ensure`: with `strategies`, the other strategy's stops are not 'gone'."""
    b, clock, om, ps, asked = shared(50)
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    (rb,) = ps.stops_of(KB)
    assert ps.ensure({KA: 30.0}, PX, clock(), DAY, strategies={"rule"}) == []
    assert ps.stops_of(KB) == [rb] and om.get(rb.coid).open
    ps.ensure({KA: 25.0}, PX, clock(), DAY, strategies={"rule"})  # a shrink of rule's own stop still goes out
    assert qty_of(ps, KA) == 25 and qty_of(ps, KB) == 20
    ps.ensure({}, PX, clock(), DAY, strategies={"rule"})  # rule's sleeve is gone: only its stop is cancelled
    assert ps.stops_of(KA) == [] and ps.stops_of(KB) == [rb]
    never_over(asked)


def test_192_a_shrink_goes_out_before_a_grow_in_the_same_call_so_both_land_at_once():
    b, clock, om, ps, asked = shared(50)
    ka, kz = ("alpha", "A"), ("zeta", "A")
    px = {ka: 90.0, kz: 90.0}
    ps.ensure({ka: 20.0, kz: 30.0}, px, clock(), DAY)
    assert resting(om) == 50
    # zeta sold 10 and alpha bought 10 (both booked): venue 50; alpha (sorted first) grows, zeta shrinks
    did = ps.ensure({ka: 30.0, kz: 20.0}, px, clock(), DAY)
    assert (qty_of(ps, ka), qty_of(ps, kz)) == (30, 20) and resting(om) == 50, did
    never_over(asked)


def test_192_an_open_sale_in_the_symbol_holds_its_shares_against_every_stop():
    b, clock, om, ps, asked = shared(50)
    ps.ensure({KA: 30.0, KB: 10.0}, PX, clock(), DAY)  # trend bought 20, its stop covers 10 (partly booked)
    assert ps.release(KB, 0)
    from kaibot.broker.protocol import OrderRequest
    coid = om.next_coid("1", "trend", "A", DAY, "sell")
    om.submit(OrderRequest("A", "sell", 20, "limit", "gtc", coid, limit_price=150.0), strategy="trend",
              purpose="sell", session=DAY)  # trend's sale of 20 rests
    ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)  # the stale sleeve must not cover the shares on sale
    assert ps.stops_of(KB) == [] and resting(om) + 20 <= 50
    never_over(asked)


def test_192_random_sequences_never_ask_for_more_shares_than_the_venue_leaves_free():
    """Property: across random sleeves, venue drifts (stale or external), re-arms and failed reads, every stop
    request fits the venue's free shares, each sleeve's stop stays within its sleeve, and after every call that
    could read the position the resting stops do not exceed it."""
    rng = random.Random(192)
    for trial in range(40):
        b, clock, om, ps, asked = shared(0, confirm_polls=2)
        b.pos.pop("A")
        keys = [("rule", "A"), ("trend", "A"), ("mean", "A")]
        sleeves: dict = {}
        real = b.positions
        for step in range(25):
            for k in keys:
                if rng.random() < 0.4:
                    sleeves[k] = float(rng.choice([0, 5, 10, 17, 20, 30]))
            sleeves = {k: v for k, v in sleeves.items() if v > 0}
            venue_qty = sum(sleeves.values()) + rng.choice([0, 0, 0, -5, -10, 3])  # stale or ahead sometimes
            if venue_qty > 0:
                b.pos["A"] = {"qty": venue_qty, "avg_entry_price": 100.0}
            else:
                b.pos.pop("A", None)
            read_ok = rng.random() >= 0.15
            if not read_ok:
                def down():
                    raise BrokerError(503, "unavailable")
                b.positions = down
            else:
                b.positions = real
            if rng.random() < 0.1:
                clock.set(clock() + timedelta(days=rng.choice([30, 45, 85])))
            prices = {k: 90.0 + rng.choice([0, 0, 1, -1]) for k in sleeves}
            n = len(asked)
            ps.ensure(sleeves, prices, clock(), DAY)
            b.positions = real
            if not read_ok:  # a lone sleeve's first stop still goes out at the sleeve on a failed read (#118)
                assert all(a[0] == "submit" for a in asked[n:] if a[1] > a[2] + 1e-9), asked[n:]
                del asked[n:]
            held = float((b.pos.get("A") or {}).get("qty") or 0)
            if read_ok:  # a drop outside our orders is only seen (and the stops shrunk) once the read works
                assert resting(om) <= held + 1e-9, (trial, step, resting(om), held)
            for k in keys:
                assert qty_of(ps, k) <= int(sleeves.get(k, 0)), (trial, step, k)
        never_over(asked)


# ---------------------------------------------------------------- QA-Ops review of #195 (after #196 merged)


def _failsafe(b, clock, strategy, qty, stop=90.0):
    from kaibot.broker.protocol import OrderRequest
    from kaibot.engine.watchdog import failsafe_coid
    return b.submit(OrderRequest("A", "sell", qty, "stop", "gtc", failsafe_coid(strategy, "A", clock()),
                                 stop_price=stop))


def test_195_review_a_sale_filled_at_the_venue_but_not_polled_yet_holds_no_shares_against_the_stop():
    """The #196 drill's case: only the unfilled remaining quantity of working sell orders counts. A sale of 20
    filled at the venue (position 25 after a purchase) while its record is still open in the manager: the adopted
    stop grows to 25, not to 25 - 20 = 5."""
    from kaibot.broker.protocol import OrderRequest
    b, clock, om, ps, asked = shared(30)
    k = ("trend_momentum", "A")
    _failsafe(b, clock, "trend_momentum", 30)
    ps.ensure({k: 30.0}, {k: 90.0}, clock(), DAY)
    (stop,) = ps.stops_of(k)
    assert ps.release(k, 10)
    coid = om.next_coid("1", "trend_momentum", "A", DAY, "sell")
    sale = om.submit(OrderRequest("A", "sell", 20, "limit", "gtc", coid, limit_price=150.0),
                     strategy="trend_momentum", purpose="sell", session=DAY)
    b.orders[sale.order_id].update(status="filled", filled_qty=20)  # filled at the venue, not polled yet
    b.pos["A"]["qty"] = 25
    assert om.get(coid).open  # the manager still thinks the sale works
    ps.ensure({k: 25.0}, {k: 92.0}, clock(), DAY)
    (s,) = ps.stops_of(k)
    assert s.coid == stop.coid and (int(s.qty), s.stop) == (25, 92.0)
    never_over(asked)


def test_195_review_two_sleeves_one_with_an_adopted_fail_safe_stop_share_the_position():
    b, clock, om, ps, asked = shared(50)
    _failsafe(b, clock, "trend", 20)  # the watchdog re-created trend's stop while the engine was down
    did = ps.ensure({KA: 30.0, KB: 20.0}, PX, clock(), DAY)
    (rb,) = ps.stops_of(KB)
    assert rb.coid.startswith("kbwd-") and any(d.startswith("adopt ") for d in did)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (30, 20) and resting(om) == 50
    b.pos["A"]["qty"] = 55  # rule bought 5 more, the caller's sleeve already says 40
    ps.ensure({KA: 40.0, KB: 20.0}, PX, clock(), DAY)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (35, 20)  # capped at what trend's adopted stop leaves free
    never_over(asked)


def test_195_review_a_fail_safe_stop_that_is_not_adopted_still_holds_its_shares():
    """A fail-safe stop of a strategy outside the call (scoped by `strategies`) is not in the manager: the venue's
    open orders still count it."""
    b, clock, om, ps, asked = shared(50)
    _failsafe(b, clock, "trend", 20)
    ps.ensure({KA: 40.0}, PX, clock(), DAY, strategies={"rule"})
    assert qty_of(ps, KA) == 30 and ps.stops_of(KB) == []
    never_over(asked)


# ---------------------------------------------------------------- #197 relay: a stop's resting quantity, not its total


def test_197_relay_a_partly_filled_fail_safe_stop_is_sized_by_what_still_rests():
    """A watchdog stop of 30 sold 10 while the engine was down (booked at restart): the sleeve is 20 and 20 still
    rest, so nothing is resized. Alpaca's replace takes the order's TOTAL qty: a resize to 15 resting sends 25, a
    release keeping 5 sends 15, never a total equal to the sleeve (which would leave 10 shares unprotected)."""
    b, clock, om, ps, asked = shared(30, new_id=False)
    k = ("trend", "A")
    o = _failsafe(b, clock, "trend", 30)
    b.orders[o["id"]].update(filled_qty=10, status="partially_filled")  # sold 10 while the engine was down
    b.pos["A"]["qty"] = 20
    ps.ensure({k: 20.0}, {k: 90.0}, clock(), DAY)
    (r,) = ps.stops_of(k)
    assert r.coid.startswith("kbwd-") and (r.qty, r.cum) == (30, 10)
    r.booked = r.cum  # the 10-share fill is booked to the sleeve
    sent = []
    real = b.replace
    b.replace = lambda oid, **kw: sent.append(kw.get("qty")) or real(oid, **kw)
    assert ps.ensure({k: 20.0}, {k: 90.0}, clock(), DAY) == [] and sent == []  # 20 rest for a sleeve of 20
    ps.ensure({k: 15.0}, {k: 90.0}, clock(), DAY)
    assert sent == [25] and qty_of(ps, k) == 15
    assert ps.release(k, 5)
    assert sent == [25, 15] and qty_of(ps, k) == 5
    b.pos["A"]["qty"] = 20  # back to 20 (a purchase, booked): the stop grows to 20 resting, 30 in all
    ps.ensure({k: 20.0}, {k: 90.0}, clock(), DAY)
    assert sent[-1] == 30 and qty_of(ps, k) == 20
    never_over(asked)


def test_197_relay_a_fail_safe_stop_whose_venue_id_the_manager_already_knows_is_not_adopted_again():
    b, clock, om, ps, asked = shared(30)
    k = ("trend", "A")
    o = _failsafe(b, clock, "trend", 30)
    ps.ensure({k: 30.0}, {k: 90.0}, clock(), DAY)
    (r,) = ps.stops_of(k)
    om.orders.pop(r.coid)  # the record is known by its venue id only (e.g. followed under another client id)
    om.orders["other-coid"] = r
    n = len([1 for _, _, kk, _ in om.journal.entries(kinds=("stop_adopted",))])
    ps.adopt({k: 30.0}, DAY)
    assert len([1 for _, _, kk, _ in om.journal.entries(kinds=("stop_adopted",))]) == n
    assert o["id"] in om.by_id
