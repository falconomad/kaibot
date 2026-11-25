"""Engine audit 2, 5.9: a refused sale is tried again, and an unfilled reduction is sent again.

Before: a sale the venue refused was not sent again that session and the send was marked complete; the next
session's retry (`_pending`) took only full exits, so a `resize` down that did not fill waited for the rule's next
decision. Now (engine/live_daily.py `send`, `_refused_sales`, `_pending_cuts`):
- a refused sale is sent again at every tick of the window under a new attempt (the venue took nothing), and the
  send stays open; a sale still refused when the window ends raises `multi_day_sale_refused`, and the sale goes
  out at the next session as a pending sale;
- a reduction that did not fill (in whole or in part) is sent again at its target on the next session without a
  new target list;
- nothing reaches the venue twice: the simulation's invariants (one working order per decision, no sale beyond
  the position) hold, and the venue's own books are counted here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "dst"))

from scenarios import quiet  # noqa: E402
from world import D1, D2, D3, NY, World, run_scenario  # noqa: E402

from kaibot.broker.faulty import Forced  # noqa: E402
from kaibot.engine import live_daily as ld  # noqa: E402
from kaibot.engine.targets import Target  # noqa: E402

DAY2, DAY3 = D2.replace("-", ""), D3.replace("-", "")


def venue_orders(w, symbol, side):
    return [o for o in w.sim.orders.values() if o["symbol"] == symbol and o["side"] == side]


def events(w, name):
    return [r for r in w.replica.tables.get("event", []) if r.get("type") == name]


def trace_lines(res, kind, part):
    return [t for t in res.trace if f" {kind} " in t and part in t]


# ---------------------------------------------------------------- a refused sale
def test_a_sale_the_venue_refuses_once_goes_out_at_the_next_tick_and_fills_once():
    w = World(quiet({D1: ("B",), D2: ()}, forced=(Forced("submit_refused", f"-B-{DAY2}-sell-", 1),)))
    res = w.run()
    assert res.ok, res.report()
    intents = trace_lines(res, "om_intent", f"-B-{DAY2}-sell-")
    assert [t[6:11] for t in intents] == ["15:50", "15:51"], intents  # refused at 15:50, sent again at 15:51
    assert any("state=rejected" in t and "refused the order" in t for t in trace_lines(res, "om_state", "sell-1"))
    sold = venue_orders(w, "B", "sell")
    assert len(sold) == 1 and sold[0]["client_order_id"].endswith("-sell-2") and sold[0]["status"] == "filled"
    assert "B" not in w.sim.pos or float(w.sim.pos["B"]["qty"]) == 0
    sent = trace_lines(res, "md_sent", f"session={D2}")
    assert len(sent) == 1 and sent[0][6:11] == "15:51"  # complete only once the sale went out
    assert not events(w, "multi_day_sale_refused")


def test_a_sale_refused_all_window_raises_the_alarm_and_goes_out_next_session():
    w = World(quiet({D1: ("B",), D2: ()}, forced=(Forced("submit_refused", "|B|sell", 99),)))
    res = w.run()
    assert res.ok, res.report()
    tries = trace_lines(res, "om_intent", f"-B-{DAY2}-sell-")
    assert [t[6:11] for t in tries] == [f"15:5{m}" for m in range(7)], tries  # each minute to the window's end
    alarm = events(w, "multi_day_sale_refused")
    assert len(alarm) == 1 and alarm[0]["payload"]["session"] == D2
    assert [s[1] for s in alarm[0]["payload"]["sales"]] == ["B"]
    sold = venue_orders(w, "B", "sell")  # the world is healed before D3: the pending sale goes out then, once
    assert len(sold) == 1 and f"-B-{DAY3}-sell-1" in sold[0]["client_order_id"] and sold[0]["status"] == "filled"


def test_a_refused_buy_is_still_not_sent_again_that_day():
    w = World(quiet({D1: ("B",)}, forced=(Forced("submit_refused", "|B|buy", 1),)))
    res = w.run()
    tries = trace_lines(res, "om_intent", "-B-20260803-buy-")
    assert len(tries) == 1  # unchanged: the venue's "no" to a buy stands for the session
    assert not events(w, "multi_day_sale_refused")


# ---------------------------------------------------------------- an unfilled reduction
def plant_resize(mp, w, day: str, symbol: str = "B"):
    """The decision core is not under test: on `day`, the session's plan reduces `symbol` to half its shares (a
    `resize` the momentum rule makes when a held name's weight falls)."""
    orig = ld.plan_session

    def planted(builder, tl, name, held, pending, data, i, eq, cash, forced=frozenset()):
        plan, targets = orig(builder, tl, name, held, pending, data, i, eq, cash, forced)
        if tl is not None and symbol in held and w.clock().astimezone(NY).date().isoformat() == day:
            half = int(float(held[symbol][0])) // 2
            targets = [t for t in targets if t.symbol != symbol] + [Target(name, symbol, half, "resize",
                                                                           {"why": float(half)})]
        return plan, targets

    mp.setattr(ld, "plan_session", planted)


@pytest.mark.parametrize("fault", [Forced("hold", f"-B-{DAY2}-resize-", "never"),
                                   Forced("partial_fill", f"-B-{DAY2}-resize-", 0.5)],
                         ids=["did_not_fill", "filled_in_part"])
def test_an_unfilled_resize_is_sent_again_next_session_and_never_twice(monkeypatch, fault):
    w = World(quiet({D1: ("B",), D2: ("B",)}, forced=(fault,)))
    plant_resize(monkeypatch, w, D2)
    res = w.run()
    assert res.ok, res.report()
    bought = venue_orders(w, "B", "buy")
    assert len(bought) == 1
    start = float(bought[0]["filled_qty"])
    target = int(start) // 2
    sends = trace_lines(res, "md_send", "'B'")
    assert any(f"session={D2}" in t and f"['B', {target}, 'resize'" in t for t in sends), sends
    assert any(f"session={D3}" in t and f"['B', {target}, 'resize'" in t for t in sends), sends  # sent again
    sold = venue_orders(w, "B", "sell")
    assert [o["client_order_id"].split("-")[-3:] for o in sold] == [[DAY2, "resize", "1"], [DAY3, "resize", "1"]]
    first = float(sold[0]["filled_qty"])
    assert float(sold[1]["qty"]) == pytest.approx(start - first - target)  # only what is still to sell
    assert float(w.sim.pos["B"]["qty"]) == pytest.approx(target)


def test_a_filled_resize_is_not_sent_again(monkeypatch):
    w = World(quiet({D1: ("B",), D2: ("B",)}))
    plant_resize(monkeypatch, w, D2)
    res = w.run()
    assert res.ok, res.report()
    sold = venue_orders(w, "B", "sell")
    assert len(sold) == 1 and f"-{DAY2}-resize-1" in sold[0]["client_order_id"]
    assert not any(f"session={D3}" in t and "'resize'" in t for t in trace_lines(res, "md_send", ""))


def test_the_quiet_run_is_unchanged():
    """No fault, no reduction: the story of test_dst's quiet run, line for line the same kinds."""
    res = run_scenario(quiet({D1: ("B",)}))
    assert res.ok and res.stats["faults"] == 0
    assert not [t for t in res.trace if "sale_refused" in t]
