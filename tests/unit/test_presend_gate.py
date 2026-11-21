"""M9 ticket 1.16 (issue #441): the pre-send gate before near-close market orders.

The audit's finding (engine_audit/report.md, "confirmed absent"): no quote, halt status or intraday move is read
before the near-close DAY market orders go out, and the share count was sized at the previous close. These tests
fail on main @ 453bb87 (no `PreSendGate`, no `presend` in the target loop, no `quotes` on the live daily path).

The gate (engine/execution.PreSendGate, pure): a halted stock, a quote that is not fresh, a move since the sizing
close outside the collar, or a buy worth more than the cap is WITHHELD; a missing quote falls back to the prior
close (the order goes out, checked at that price). A withheld order is not dropped: the live daily path sends it
again at the next session (a buy through the carry-over of Amendments 11 to 13, a sale to zero as a pending sale, a
withheld reduction as a reduction).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import numpy as np
import pytest
from test_live_daily import EQ0, I0, POL, Harness
from test_order_manager import DAY, et, manager, venue

from kaibot.broker.sim import NY
from kaibot.engine import daily_report as dr
from kaibot.engine import live_daily as ld
from kaibot.engine.daily import DailyData, momentum_limits
from kaibot.engine.execution import ExecutionPolicy, PreSendGate, Quote, quote_from_alpaca
from kaibot.engine.targets import Target, reconcile_targets
from kaibot.strategies import momentum as mom

NOW = datetime(2026, 11, 20, 20, 51, tzinfo=UTC)  # 15:51 ET
GATE = PreSendGate()


def q(bid, ask, age_s=1.0, now=NOW) -> Quote:
    return Quote(bid, ask, now - timedelta(seconds=age_s))


@pytest.fixture(scope="module")
def data() -> DailyData:
    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


# ---------------------------------------------------------------- the pure gate


def test_a_fresh_quote_inside_the_collar_passes():
    assert GATE.check("buy", 10, close=100.0, quote=q(100.9, 101.1), now=NOW, max_value=5_000.0) is None
    assert GATE.check("sell", 10, close=100.0, quote=q(95.0, 95.2), now=NOW, max_value=None) is None


def test_a_halted_stock_is_withheld_whatever_its_quote():
    why = GATE.check("sell", 10, close=100.0, quote=q(100.0, 100.1), now=NOW, halted=True)
    assert why and "halted" in why


def test_a_stale_quote_is_withheld_as_not_trading():
    why = GATE.check("buy", 10, close=100.0, quote=q(100.0, 100.1, age_s=GATE.max_age_s + 1), now=NOW)
    assert why and "fresh" in why
    assert GATE.check("buy", 10, close=100.0, quote=q(100.0, 100.1, age_s=GATE.max_age_s - 1), now=NOW) is None


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("gap", [0.20, -0.20])
def test_a_twenty_percent_gap_since_the_sizing_close_is_withheld(side, gap):
    px = 100.0 * (1 + gap)
    why = GATE.check(side, 10, close=100.0, quote=q(px - 0.05, px + 0.05), now=NOW)
    assert why and "sizing close" in why and "20" in why


def test_the_collar_reads_the_side_of_the_quote_nearest_the_close():
    """IEX is one exchange's book: one side can sit far from the market. Only a quote whose bid AND ask are both
    outside the collar is a gap."""
    assert GATE.check("buy", 10, close=100.0, quote=q(99.9, 130.0), now=NOW) is None
    assert GATE.check("buy", 10, close=100.0, quote=q(0.0, 100.2), now=NOW) is None  # one-sided: the side it has
    assert GATE.check("buy", 10, close=100.0, quote=q(119.0, 130.0), now=NOW) is not None


def test_a_buy_above_the_value_cap_is_withheld_and_a_sale_never_is():
    why = GATE.check("buy", 100, close=100.0, quote=q(100.0, 100.1), now=NOW, max_value=9_000.0)
    assert why and "cap" in why
    assert GATE.check("buy", 100, close=100.0, quote=q(100.0, 100.1), now=NOW, max_value=10_500.0) is None
    # a sale only reduces exposure and can never be larger than the shares held: the cap is not its check
    assert GATE.check("sell", 100, close=100.0, quote=q(100.0, 100.1), now=NOW, max_value=9_000.0) is None


def test_a_missing_quote_falls_back_to_the_prior_close():
    """No quote (the read failed, the feed has none, or it has no price): the order is checked at the prior close
    and goes out; a market-data outage must not become one more way to miss a rebalance."""
    assert GATE.check("buy", 10, close=100.0, quote=None, now=NOW, max_value=5_000.0) is None
    assert GATE.check("buy", 10, close=100.0, quote=q(0.0, 0.0), now=NOW, max_value=5_000.0) is None
    assert GATE.check("sell", 10, close=None, quote=None, now=NOW) is None
    why = GATE.check("buy", 100, close=100.0, quote=None, now=NOW, max_value=9_000.0)  # the cap, at the prior close
    assert why and "cap" in why
    assert GATE.check("buy", 10, close=None, quote=q(100.0, 100.1), now=NOW, max_value=5_000.0) is None


def test_alpacas_quote_is_read_with_its_nanosecond_timestamp():
    got = quote_from_alpaca({"bp": 99.5, "ap": 100.5, "bs": 1, "as": 2, "t": "2026-11-20T20:50:59.123456789Z"})
    assert got == Quote(99.5, 100.5, datetime(2026, 11, 20, 20, 50, 59, 123456, tzinfo=UTC))
    assert quote_from_alpaca(None) is None and quote_from_alpaca({"bp": "x"}) is None
    assert quote_from_alpaca({"bp": 1.0, "ap": 1.1}) is None  # no time: its age is unknown, so it is no quote


# ---------------------------------------------------------------- the target loop


def test_the_target_loop_withholds_what_the_gate_refuses_and_sends_the_rest():
    b, clock = venue()
    clock.set(et(DAY, "15:51"))
    om, _, _ = manager(b, clock)
    seen = []

    def presend(t, side, qty):
        seen.append((t.symbol, side, qty))
        return "withheld: halted" if t.symbol == "A" else None

    want = [Target("rule", "A", 30, "buy"), Target("rule", "B", 20, "buy")]
    res = reconcile_targets(om, ExecutionPolicy("near_close"), want, {}, now=clock(), session=DAY, presend=presend)
    assert [(r.symbol, r.qty) for r in res.sent] == [("B", 20)]
    assert res.withheld == [(("rule", "A"), "buy", "withheld: halted")] and not res.skipped
    assert seen == [("A", "buy", 30), ("B", "buy", 20)]
    assert [o["symbol"] for o in b.orders.values()] == ["B"]
    # no order, so no attempt number is used up: the order that goes out later is the decision's first
    assert om.next_coid("0", "rule", "A", DAY, "buy").endswith("-1")


# ---------------------------------------------------------------- the live daily path: withheld, then carried


class Quoted(Harness):
    """The harness with a quote source: every stock quotes at today's close a second ago, except what a test edits
    (`stale`: symbols without a fresh quote today, `gap`: symbol -> move since the prior close)."""

    stale: dict = {}
    gap: dict = {}
    down = False
    asked: list

    def build(self) -> None:
        super().build()
        self.asked = getattr(self, "asked", [])
        self.live.quotes = self.quotes

    def quotes(self, symbols):
        self.asked.append(list(symbols))
        if self.down:
            raise RuntimeError("market data down")
        now, d = self.clock(), self.d
        day = now.astimezone(NY).date()
        i = d.index_of(day)
        out = {}
        for s in symbols:
            px = float(d.raw_c[i - 1, d.col(s)]) * (1 + self.gap.get((str(day), s), 0.0))
            if not np.isfinite(px):
                continue
            age = timedelta(hours=1) if (str(day), s) in self.stale else timedelta(seconds=1)
            out[s] = {"bp": round(px - 0.01, 2), "ap": round(px + 0.01, 2),
                      "t": (now - age).isoformat().replace("+00:00", "Z")}
        return out


def _orders_on(h, day):
    return [o for o in h.broker.orders.values()
            if datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date() == day]


def _first_plan(data, tmp_path):
    """A dry run without the gate: the first session with buys, and its plan."""
    s = mom.VERSIONS["trend_momentum"]
    h = Harness(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path / "dry")
    h.run()
    first = min(h.broker.orders.values(), key=lambda o: int(o["id"][1:]))
    day = datetime.fromisoformat(first["submitted_at"]).astimezone(NY).date()
    buys = sorted(o["symbol"] for o in _orders_on(h, day) if o["side"] == "buy")
    assert len(buys) >= 3
    return s, day, buys, h


def test_a_halted_stock_and_a_twenty_percent_gap_are_withheld_and_carried_to_the_next_session(data, tmp_path):
    (tmp_path / "dry").mkdir()
    s, day, buys, dry = _first_plan(data, tmp_path)
    halted, gapped = buys[0], buys[1]
    h = Quoted(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    h.stale = {(str(day), halted)}
    h.gap = {(str(day), gapped): 0.20}
    h.run()
    # the fill session: neither order reached the venue, every other order did, exactly as without the gate
    got = sorted((o["symbol"], o["side"], float(o["qty"])) for o in _orders_on(h, day))
    want = sorted((o["symbol"], o["side"], float(o["qty"])) for o in _orders_on(dry, day)
                  if o["symbol"] not in (halted, gapped))
    assert got == want and got
    held = [b for *_, b in h.journal.entries(kinds=("md_withheld",)) if b["session"] == str(day)]
    assert {b["symbol"] for b in held} == {halted, gapped} and all(b["side"] == "buy" for b in held)
    why = {b["symbol"]: b["why"] for b in held}
    assert "fresh" in why[halted] and "sizing close" in why[gapped]
    assert len(held) == 2  # journalled once per order, not once per tick of the window
    ev = [b for k, b in h.events if k == "multi_day_orders" and b["session"] == str(day)]
    assert ev and {w[0] for w in ev[-1]["withheld"]} == {halted, gapped} and ev[-1]["retry"] is True
    # the next session: both are carried at the plan's own target shares, through the carry-over
    nxt = data.day(data.index_of(day) + 1)
    sends = {b["session"]: b for *_, b in h.journal.entries(kinds=("md_send",)) if b["strategy"] == s.name}
    plan = {t[0]: t[1] for t in sends[str(day)]["targets"] if t[2] in ("buy", "resize")}
    carried = {c[0]: c[1] for c in sends[str(nxt)]["carry"]["carried"]}
    assert carried == {halted: plan[halted], gapped: plan[gapped]}
    sent = {o["symbol"]: o for o in _orders_on(h, nxt) if o["symbol"] in (halted, gapped)}
    assert {k: (float(o["qty"]), o["status"]) for k, o in sent.items()} == {
        halted: (float(plan[halted]), "filled"), gapped: (float(plan[gapped]), "filled")}
    assert all("-carry-" in o["client_order_id"] for o in sent.values())
    assert not [b for *_, b in h.journal.entries(kinds=("md_withheld",)) if b["session"] == str(nxt)]


def test_a_withheld_order_goes_out_later_in_the_same_window_once_its_quote_is_fresh(data, tmp_path):
    (tmp_path / "dry").mkdir()
    s, day, buys, dry = _first_plan(data, tmp_path)
    sym = buys[0]
    h = Quoted(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    h.stale = {(str(day), sym)}
    real = h.live.send

    def send(session):  # the halt ends at 15:53
        et_now = h.clock().astimezone(NY)
        if et_now.date() == day and et_now.time() >= time(15, 53):
            h.stale = {}
        return real(session)

    h.live.send = send
    h.run()
    mine = [o for o in _orders_on(h, day) if o["symbol"] == sym]
    assert len(mine) == 1 and mine[0]["status"] == "filled" and mine[0]["client_order_id"].endswith("-buy-1")
    assert datetime.fromisoformat(mine[0]["submitted_at"]).astimezone(NY).time() == time(15, 53)
    assert sorted((o["symbol"], float(o["qty"])) for o in _orders_on(h, day)) == sorted(
        (o["symbol"], float(o["qty"])) for o in _orders_on(dry, day))
    assert [b for *_, b in h.journal.entries(kinds=("md_sent",)) if b["session"] == str(day)]  # the step completed


def test_a_withheld_sale_is_sent_again_at_the_next_session(data, tmp_path):
    (tmp_path / "dry").mkdir()
    s, _, _, dry = _first_plan(data, tmp_path)
    sale = min((o for o in dry.broker.orders.values() if o["side"] == "sell"), key=lambda o: int(o["id"][1:]))
    day = datetime.fromisoformat(sale["submitted_at"]).astimezone(NY).date()
    sym = sale["symbol"]
    h = Quoted(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    h.gap = {(str(day), sym): -0.20}
    h.run()
    assert not [o for o in _orders_on(h, day) if o["symbol"] == sym]
    held = [b for *_, b in h.journal.entries(kinds=("md_withheld",)) if b["session"] == str(day)]
    assert [(b["symbol"], b["side"]) for b in held] == [(sym, "sell")]
    nxt = data.day(data.index_of(day) + 1)
    again = [o for o in _orders_on(h, nxt) if o["symbol"] == sym]
    assert [(o["side"], float(o["qty"]), o["status"]) for o in again] == [("sell", float(sale["qty"]), "filled")]


def test_without_quotes_the_orders_go_out_at_the_prior_close_exactly_as_before(data, tmp_path):
    """The feed is down for the whole run: every order of the run without the gate still goes out (the fallback)."""
    (tmp_path / "dry").mkdir()
    s, _, _, dry = _first_plan(data, tmp_path)
    h = Quoted(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    h.down = True
    h.run()
    key = lambda o: (o["submitted_at"][:10], o["symbol"], o["side"], float(o["qty"]))  # noqa: E731
    assert sorted(map(key, h.broker.orders.values())) == sorted(map(key, dry.broker.orders.values()))
    assert h.asked and not list(h.journal.entries(kinds=("md_withheld",)))
    assert any(k == "multi_day_error" and b.get("step") == "presend_quotes" for k, b in h.events)


def test_with_fresh_quotes_at_the_close_the_gate_changes_no_order(data, tmp_path):
    (tmp_path / "dry").mkdir()
    s, _, _, dry = _first_plan(data, tmp_path)
    h = Quoted(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    h.run()
    key = lambda o: (o["submitted_at"][:10], o["symbol"], o["side"], float(o["qty"]))  # noqa: E731
    assert sorted(map(key, h.broker.orders.values())) == sorted(map(key, dry.broker.orders.values()))
    assert not list(h.journal.entries(kinds=("md_withheld",)))
    # one quote read per tick that has something to send, never one per order
    assert len(h.asked) < len(dry.broker.orders)


def test_the_gate_is_off_without_a_quote_source(data, tmp_path):
    """Research and replays build the path without `quotes`: nothing is read and nothing changes (the bit-identity
    of the ten-year runs)."""
    s = mom.VERSIONS["trend_momentum"]
    h = Harness(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    assert h.live.quotes is None and EQ0 and I0


def test_a_withheld_reduction_is_sent_again_as_the_same_reduction(data, tmp_path):
    """A resize down (not to zero) is neither a pending sale nor a carried buy: the gate's own record carries it."""
    (tmp_path / "dry").mkdir()
    s, _, _, dry = _first_plan(data, tmp_path)
    trims = [o for o in dry.broker.orders.values() if o["side"] == "sell" and "-resize-" in o["client_order_id"]]
    assert trims, "the fixture has no reduction"
    trim = min(trims, key=lambda o: int(o["id"][1:]))
    day = datetime.fromisoformat(trim["submitted_at"]).astimezone(NY).date()
    sym = trim["symbol"]
    h = Quoted(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    h.stale = {(str(day), sym)}
    h.run()
    assert not [o for o in _orders_on(h, day) if o["symbol"] == sym]
    nxt = data.day(data.index_of(day) + 1)
    again = [o for o in _orders_on(h, nxt) if o["symbol"] == sym]
    assert [(o["side"], float(o["qty"]), o["status"]) for o in again] == [("sell", float(trim["qty"]), "filled")]
    send = [b for *_, b in h.journal.entries(kinds=("md_send",)) if b["session"] == str(nxt)][-1]
    assert [t for t in send["targets"] if t[0] == sym][0][2:] == ["resize", "withheld"]
    sells = [o for o in h.broker.orders.values() if o["symbol"] == sym and "withheld" not in o["client_order_id"]
             and o["side"] == "sell" and datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date() in (day, nxt)]
    assert len(sells) == 1  # sent once


def test_the_engines_quote_read_has_a_deadline_and_other_data_clients_get_no_gate():
    from types import SimpleNamespace

    from kaibot.engine.alpaca import AlpacaData

    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"quotes": {"A": {"bp": 1.0, "ap": 1.1, "t": "2026-11-20T20:50:59Z"}}}'

    def opener(req, timeout):
        calls.append((req.full_url, timeout))
        return Resp()

    d = AlpacaData("k", "s")
    d.http._open = opener
    got = d.quotes_within(["A", "B"], 8.0)
    assert set(got) == {"A"} and "symbols=A%2CB" in calls[0][0] and "feed=iex" in calls[0][0]
    assert calls[0][1] <= 8.0 and d.http.deadline_s is None and d.http.timeout_s == 30.0  # the plain client: as it was
    fn = ld.presend_quotes(SimpleNamespace(market=SimpleNamespace(data=d)))
    assert set(fn(("A", "B"))) == {"A"} and calls[1][1] <= ld.PRESEND_QUOTE_DEADLINE_S
    replay = SimpleNamespace(market=SimpleNamespace(data=SimpleNamespace(latest_quotes=lambda s: {})))
    assert ld.presend_quotes(replay) is None and ld.presend_quotes(SimpleNamespace()) is None
