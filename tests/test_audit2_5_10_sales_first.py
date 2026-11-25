"""Engine audit 2, 5.10 (the residual of M9 1.5): one failing `positions()` call no longer blocks the whole send.

Before: `_check_drift` read the broker's positions with no handler. When that endpoint alone failed, every minute
raised there; the next minute was not "clean", so the send (sales included) stayed behind that step, and the loop's
error streak tripped `engine_errors`. Now the drift check contains its own failure: the `positions` hold stops new
buys until a read works again, sales and stops go on, and the minute completes, so the next minute's send runs
first again (engine/book_loop.py). The DST world below has the positions endpoint answer 429 all through D2's
send window (15:00-15:59 ET): the due sale goes out in the window, the buy waits, and goes out once the endpoint
reads again (D3, as a carried buy).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "dst"))

from scenarios import quiet  # noqa: E402
from world import D1, D2, D3, NY, World  # noqa: E402

from kaibot.broker.faulty import Forced  # noqa: E402
from kaibot.engine.state import BLOCK_CODES, ENTRY_SOURCES, TradingState  # noqa: E402

DAY2, DAY3 = D2.replace("-", ""), D3.replace("-", "")
POSITIONS_DOWN = Forced("rate_limit", f"positions|{DAY2}T19")  # 15:00-15:59 ET on D2 (EDT): every positions() read


def venue_orders(w, symbol, side):
    return [o for o in w.sim.orders.values() if o["symbol"] == symbol and o["side"] == side]


def test_positions_unreadable_all_window_the_sale_goes_out_and_the_buy_is_held():
    w = World(quiet({D1: ("B",), D2: ("C",)}, forced=(POSITIONS_DOWN,)))
    seen = []
    tick = w.one_pass

    def watched():  # what the engine's switch says while the endpoint is down, in the window
        tick()
        now = w.clock().astimezone(NY)
        if w.eng is not None and now.date().isoformat() == D2 and "15:50" <= now.strftime("%H:%M") < "15:57":
            seen.append((now.strftime("%H:%M"), w.eng.trading_state().state, w.eng.entry_reason()))

    w.one_pass = watched
    res = w.run()
    assert res.ok, res.report()
    assert any("fault rate_limit positions" in t for t in res.trace)
    sold = venue_orders(w, "B", "sell")
    assert len(sold) == 1 and f"-B-{DAY2}-sell-1" in sold[0]["client_order_id"] and sold[0]["status"] == "filled"
    at = datetime.fromisoformat(sold[0]["submitted_at"]).astimezone(NY)
    assert at.strftime("%H:%M") == "15:50"  # the window's first minute
    bought = venue_orders(w, "C", "buy")
    assert len(bought) == 1 and f"-C-{DAY3}-" in bought[0]["client_order_id"]  # not on D2: held, then carried
    assert seen and all(st is TradingState.REDUCING_ONLY and why[0] == "positions_unread" for _, st, why in seen)
    assert res.stats["tick_errors"] == 0  # contained: no minute failed, no engine_errors halt
    assert not [r for r in w.replica.tables.get("halt", []) if r.get("kind") == "engine_errors"]
    spells = [r for r in w.replica.tables.get("event", []) if r.get("type") == "engine_error"
              and (r.get("payload") or {}).get("step") == "drift_check"]
    assert len(spells) == 1  # one event for the spell, not one a minute


def test_the_hold_is_the_last_source_and_has_its_code():
    assert ENTRY_SOURCES[-1] == "positions" and "positions_unread" in BLOCK_CODES
