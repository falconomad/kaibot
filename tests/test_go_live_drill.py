"""QA-Ops go-live drill (Product, 16 Nov 2026, before the first multi-day trade on Wed 18 Nov): the real engine path
with KAIBOT_MULTIDAY=on, one decision evening and its fill session, end to end on the simulated venue (SimBroker
with a cash book and cost model v1, so every sale is charged a fee):

    decision after the close -> the session's plan (`md_send`) -> 15:50 near-close send (#333) -> the fills
    -> the order manager books them -> reconciliation

with what goes live together:
- the corporate-action guard (#271, trend_momentum.md Amendment 6) on a fake Alpaca feed: one candidate has an
  announced spin-off (not bought), one held stock an announced split (kept, named on the heartbeat);
- the daily-store gate (#263): one candidate failed the integrity check tonight (left out of the view, never bought);
- the book limits of the Aggressive level ("Hunter"): $95,000 run, 100% exposure cap, 30 names, 40% per sector,
  the builder's 3% cash reserve;
- the venue's own fee on every sale (#241, the cash-drift fix): ledger cash = broker cash, to the cent.

The same session is run three times: straight, with the engine killed between the plan and the send (after the
venue took some orders, before the engine heard back), and killed between the send and the fills. Each run must
send every order once, sell only shares held, keep the caps, and end with the ledger equal to the broker.

The decision itself is fixed (`Wants`: the rule's name and holding policy, a ranked list of the view's rankable
stocks), so the drill tests the path, not the rule's signals. The daily view is read as live reads it: a daily
store on disk (bars stamped available at 20:15 ET, as scripts/fetch_daily.py writes them) through
`live_daily.StoreLoader` and its integrity gate (#263), which finds FAILED's bad bar and leaves it out; the evening
decision is taken after 20:15 ET.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, timedelta

import pandas as pd
import pytest
from fakes import Clock, FakeData, FakeStore
from test_engine import synth_frames
from test_failsafe_drills import DAY1, DAY2, MD, TwoDays
from test_live_daily_engine import SEND

from kaibot.broker.sim import COSTS_V1
from kaibot.engine import live_daily as ld
from kaibot.engine.core import Engine
from kaibot.engine.journal import Journal
from kaibot.engine.market import LiveMarket
from kaibot.replay.data import ET
from kaibot.risk.profiles import load_profiles, rule_book_limits
from kaibot.strategies import momentum as mom
from kaibot.strategies.base import TargetList

LEVEL, POT = "aggressive", 95_000.0  # the dashboard's "Hunter"
SECTORS = {"Tech": 14, "Health": 8, "Energy": 6, "Banks": 6, "Retail": 4}  # Tech alone could take far over 40%
STOCKS = [f"{sec[:3].upper()}{i}" for sec, n in SECTORS.items() for i in range(n)]
SECTOR = {s: sec for sec, n in SECTORS.items() for s in STOCKS if s.startswith(sec[:3].upper())}
SOLD, KEPT = "BAN0", "RET0"  # held from earlier: SOLD leaves the list (a sale, with a fee), KEPT stays
SPUN = "TEC1"  # an announced spin-off: not bought (corporate-action guard)
FAILED = "HEA2"  # a bar failed the integrity check tonight: left out of the view (daily-store gate)
HELD = {SOLD: 60, KEPT: 40}


class Killed(BaseException):
    """The engine process dies (not an exception the loop would catch)."""


@dataclass(frozen=True)
class Wants(mom.TrendMomentum):
    """trend_momentum's name, holding policy and sizing settings; its decision: a fixed ranked list."""

    def decision_day(self, prev, fill) -> bool:
        return True

    def on_event(self, ctx):
        # what the store gate left out tonight is not rankable: member False (#263, Market.from_panel(exclude=))
        mem = ctx.view.d.member
        k = ctx.view.k

        def rankable(sym):
            return sym in ctx.view.d._col and (mem is None or bool(mem[k, ctx.view.col(sym)]))

        ranked = [s for s in STOCKS if s != SOLD and rankable(s)]
        keep = [s for s in ranked if s in ctx.held]
        buy = [s for s in ranked if s not in ctx.held]
        sell = {s: "rank" for s in ctx.held if s not in ranked}
        return TargetList(self.name, ctx.fill_session, {s: 1.0 for s in [*keep, *buy]}, sizing="atr_risk",
                          features={"keep": keep, "buy": buy, "sell": sell}, resize=False)


def _frames() -> dict:
    """Minute bars for every name over 75 sessions to DAY2: random walks at different price levels."""
    base = synth_frames()
    f = {"SPY": base["SPY"], "XLK": base["XLK"], "A": base["A"]}
    for i, s in enumerate(STOCKS):
        a = synth_frames(seed=100 + i)["A"].copy()
        k = 20.0 + 9.0 * i  # $20 .. $350
        for c in ("o", "h", "l", "c", "vw"):
            a[c] = a[c] * k / 100.0
        f[s] = a
    return f


class Venue(TwoDays):
    """The engine tests' two-session venue with a cash book and cost model v1 (a fee on every sale), reporting cash
    and equity as Alpaca's account does."""

    def account(self):
        mtm = sum(float(p["qty"]) * float(self.bars.last_close(s, self.clk()) or 0) for s, p in self.pos.items())
        return {"status": "ACTIVE", "cash": f"{self.cash:.2f}", "equity": f"{self.cash + mtm:.2f}",
                "shorting_enabled": False}


def _feed(path, params):
    """Alpaca's /v1/corporate-actions: a spin-off of SPUN and a split of KEPT inside the 35-session window."""
    syms = set(params["symbols"].split(","))
    block = {"spin_offs": [{"source_symbol": SPUN, "new_symbol": SPUN + "N", "ex_date": "2026-08-20"}],
             "forward_splits": [{"symbol": KEPT, "ex_date": "2026-08-27", "new_rate": 2, "old_rate": 1}]}
    return {"corporate_actions": {k: [r for r in v if syms & {r.get("source_symbol"), r.get("symbol")}]
                                  for k, v in block.items()}, "next_page_token": None}


class Drill:
    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
        self.open1, self.open2 = (pd.Timestamp(f"{d} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()
                                  for d in (DAY1, DAY2))
        self.clock = Clock(self.open1 - timedelta(minutes=30))
        self.frames = _frames()
        self.data = FakeData(self.frames, self.clock)
        self.broker = Venue(self.data, self.clock, DAY1)
        self.broker.costs, self.broker.cash = COSTS_V1, POT
        self.broker.wash_check, self.broker.shorting_enabled = True, False
        self.store, self.jpath, self.tmp = FakeStore(), tmp_path / "journal.sqlite", tmp_path
        self.write_store()
        self.loader = ld.StoreLoader(self.tmp, self.universe, mode="today")  # the live loader and its gate (#263)
        s = Wants()
        lim = ld.live_limits(s, load_profiles()[LEVEL], rule_book_limits(MD, LEVEL, research=True))
        self.limits = lim
        self.rule = ld.Rule(s, ld.live_builder(s, lim))
        self.eng = self.engine()
        self.store.insert("command", {"kind": "new_run", "payload": {"amount_usd": POT, "level": LEVEL},
                                      "idempotency_key": "drill", "applied_at": None})
        self.eng.startup()
        self.eng.apply_commands()
        self.rid = self.eng.run["id"]

    def write_store(self) -> None:
        """The daily store under tmp/daily as scripts/fetch_daily.py writes it: one bar per session to DAY2, each
        stamped available at 20:15 ET; FAILED's bar of the session before DAY1 has a high below its close."""
        import json

        import pyarrow.parquet as pq

        from kaibot.ingest.daily_bars import actions_table, bars_path, bars_table

        root = self.tmp / "daily"
        bad = pd.Timestamp(DAY1).date() - timedelta(days=1)
        while bad.weekday() >= 5:
            bad -= timedelta(days=1)
        for sym in [*STOCKS, "SPY"]:
            x = self.frames[sym]
            d = x.assign(day=x["ts"].dt.tz_convert(ET).dt.date)
            g = d.groupby("day").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"),
                                     v=("v", "sum"))
            rows = [{"t": f"{day}T04:00:00Z", "o": r.o, "h": r.h, "l": r.l, "c": r.c, "v": r.v, "vw": r.c}
                    for day, r in g.iterrows()]
            if sym == FAILED:
                for r in rows:
                    if r["t"].startswith(str(bad)):
                        r["h"] = r["c"] * 0.5  # a high below the close: the gate's ohlc_order
            p = bars_path(root, sym)
            p.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(bars_table(rows, pd.Timestamp(DAY2).date()), p)
        pq.write_table(actions_table([]), root / "corporate_actions.parquet")
        self.universe = self.tmp / "universe.yaml"
        self.universe.write_text(json.dumps({"sectors": {sec: [s for s in STOCKS if SECTOR[s] == sec]
                                                         for sec in SECTORS}, "market": "SPY"}))
        self.bad_day = bad

    def load(self, k, now):
        return self.loader(k, now)

    def engine(self) -> Engine:
        from kaibot.engine.corp_guard import CorporateActionGuard  # (#271)

        market = LiveMarket(self.data, ["A"], {"A": "XLK"}, "SPY", self.tmp / "hist",
                            lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
        eng = Engine(self.broker, market, self.store, load_profiles(), set(), now=self.clock,
                     journal=Journal(self.jpath))
        eng.market.candidates_at = lambda m: pd.DataFrame()  # no intraday trades in the drill
        eng.daily = ld.for_engine(eng, self.load, rules=[self.rule], ca=CorporateActionGuard(get=_feed))
        return eng

    def restart(self) -> Engine:
        self.eng.journal.close()
        self.eng = self.engine()
        self.eng.startup()
        return self.eng

    def tick_to(self, open_utc, k0, k1):
        for k in range(k0, k1):
            bar = open_utc + timedelta(minutes=k)
            self.broker.advance(pd.Timestamp(bar))
            self.clock.t = bar + timedelta(minutes=1, seconds=7)
            self.eng.tick()

    def after_close(self, open_utc):
        self.broker.advance(pd.Timestamp(open_utc + timedelta(minutes=389)))
        self.clock.t = open_utc + timedelta(hours=7)
        self.eng.tick()

    def hold_earlier(self):
        """SOLD and KEPT were bought before DAY1 (booked by the ledger, paid at the venue)."""
        self.tick_to(self.open1, 0, 30)
        for sym, q in HELD.items():
            px = float(self.data.history.last_close(sym, self.clock()))
            self.broker.pos[sym] = {"qty": q, "avg_entry_price": px}
            self.broker.cash -= q * px
            self.eng._book("fill", {"run_id": self.rid, "strategy": MD, "symbol": sym, "side": "buy", "qty": q,
                                    "px": px, "fees": 0.0, "coid": f"kb{self.rid}-{MD}-{sym}-20260731-buy-1",
                                    "session": "2026-07-31"})
        self.tick_to(self.open1, 30, 390)
        self.after_close(self.open1)  # 16:30 ET: the store's bars of DAY1 are not available yet: no decision
        assert not list(self.eng.journal.entries(kinds=("md_decision",)))
        self.clock.t = self.open1 + timedelta(hours=11)  # 20:30 ET, after the nightly refresh's 20:15 stamp
        self.eng.tick()  # the decision for DAY2, on the store read through the gate

    def md(self):
        """The daily path's orders at the venue (near-close DAY market orders since #333)."""
        return [o for o in self.broker.orders.values()
                if str(o.get("client_order_id")).startswith(f"kb{self.rid}-{MD}-")
                and (o["type"], o["time_in_force"]) == ("market", "day")]


def run_session(tmp_path, monkeypatch, kill: str | None = None) -> Drill:
    d = Drill(tmp_path, monkeypatch)
    d.hold_earlier()
    if kill == "plan_send":  # dies at 15:50 after the venue took 3 orders, before the engine heard back
        real, n = d.broker.submit, [0]

        def submit(req):
            o = real(req)
            n[0] += 1
            if n[0] == 3:
                raise Killed("killed with the venue holding the 3rd order")
            return o

        d.broker.submit = submit
        with pytest.raises(Killed):
            d.tick_to(d.open2, 0, SEND + 1)
        d.broker.submit = real
        assert len(d.md()) == 3 and [b for *_, b in d.eng.journal.entries(kinds=("md_send",))]
        d.restart()
        d.tick_to(d.open2, SEND + 1, 390)
    elif kill == "send_fills":
        d.tick_to(d.open2, 0, SEND + 1)
        d.restart()
        d.tick_to(d.open2, SEND + 1, 390)
    else:
        d.tick_to(d.open2, 0, 390)
    d.after_close(d.open2)
    for _ in range(3):  # later ticks change nothing
        d.eng.tick()
    return d


def orders_of(d: Drill) -> list[tuple]:
    return sorted((o["symbol"], o["side"], float(o["qty"]), o["status"]) for o in d.md())


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        return {k: run_session(tmp_path_factory.mktemp(k or "straight"), mp, k)
                for k in (None, "plan_send", "send_fills")}
    finally:
        mp.undo()


@pytest.mark.parametrize("kind", [None, "plan_send", "send_fills"])
def test_every_order_once_only_held_shares_sold_and_all_filled(runs, kind):
    d = runs[kind]
    o = d.md()
    coids = [x["client_order_id"] for x in o]
    assert len(coids) == len(set(coids))
    syms = [(x["symbol"], x["side"]) for x in o]
    assert len(syms) == len(set(syms)), "one order per symbol and side"
    sells = [x for x in o if x["side"] == "sell"]
    assert [(x["symbol"], float(x["qty"])) for x in sells] == [(SOLD, float(HELD[SOLD]))]
    assert all(x["status"] == "filled" for x in o)
    assert SPUN not in {x["symbol"] for x in o} and FAILED not in {x["symbol"] for x in o}
    assert not [x for x in o if x["symbol"] == KEPT], "a held stock with an announced split is neither sold nor added"
    fills = [b for *_, b in d.eng.journal.entries(kinds=("fill",)) if b.get("session") != "2026-07-31"]
    assert len(fills) == len(o), "each fill booked once"


def test_the_three_runs_send_and_book_the_same(runs):
    base = orders_of(runs[None])
    assert len(base) > 10
    for k in ("plan_send", "send_fills"):
        assert orders_of(runs[k]) == base, k
        a, b = runs[None].eng.ledger.book(runs[None].rid), runs[k].eng.ledger.book(runs[k].rid)
        assert {s: x.qty for s, x in a.sleeves.items()} == {s: x.qty for s, x in b.sleeves.items()}, k


@pytest.mark.parametrize("kind", [None, "plan_send", "send_fills"])
def test_the_ledger_equals_the_broker_to_the_cent(runs, kind):
    d = runs[kind]
    b = d.eng.ledger.book(d.rid)
    venue = {s: float(p["qty"]) for s, p in d.broker.pos.items()}
    assert {sym: q for sym, q in b.qty_by_symbol().items() if q} == venue
    ledger_change, venue_change = b.cash - b.amount, d.broker.cash - b.broker_cash0
    assert abs(ledger_change - venue_change) < 0.005, (ledger_change, venue_change)
    sale = [x for *_, x in d.eng.journal.entries(kinds=("fill",)) if x["side"] == "sell"]
    assert len(sale) == 1 and sale[0]["fees"] > 0  # the venue's fee, booked
    venue_fee = sum(float(f["fees"]) for f in d.broker.fills if f["side"] == "sell")
    assert sale[0]["fees"] == pytest.approx(venue_fee, abs=1e-9)
    assert d.eng.recon.check("drill").diffs == []
    assert d.eng.account_clean and d.eng.kill is None


@pytest.mark.parametrize("kind", [None, "plan_send", "send_fills"])
def test_the_book_limits_hold_after_the_fills(runs, kind):
    d = runs[kind]
    lim = d.limits
    b = d.eng.ledger.book(d.rid)
    px = {s: float(d.data.history.last_close(s, d.clock())) for s in d.broker.pos}
    value = {s: q * px[s] for s, q in b.qty_by_symbol().items() if q}
    equity = b.cash + sum(value.values())
    assert len(value) <= lim.max_names == 30
    assert sum(value.values()) <= equity * lim.max_gross_pct / 100 + 1e-6
    for sec in SECTORS:
        v = sum(x for s, x in value.items() if SECTOR[s] == sec)
        assert v <= equity * lim.max_sector_pct / 100 * 1.02, (sec, v / equity)  # 2% for the auction's moves
    assert max(value.values()) <= equity * lim.max_name_pct / 100 * 1.02
    assert b.cash >= equity * 0.03 * 0.9, b.cash / equity  # the builder's 3% reserve, less the auction's moves
    assert b.cash > 0


def test_the_heartbeat_says_what_happened(runs):
    d = runs[None]
    st = d.eng.daily.status(d.open2.date())
    note = st.get("note", "")
    assert f"{KEPT}: split on 2026-08-27" in note  # the held stock's announced action
    kinds = [e["type"] for e in d.store.tables["event"]]
    skip = [e for e in d.store.tables["event"] if e["type"] == "multi_day_ca_skip"]
    assert [e["payload"]["symbol"] for e in skip] == [SPUN]
    (drop,) = [e for e in d.store.tables["event"] if e["type"] == "multi_day_view_dropped"]  # said once
    assert drop["payload"]["dropped"] == {FAILED: f"1 bad bar in 1 key: {FAILED} {d.bad_day} ohlc_order"}
    assert f"decided without {FAILED}" in (st.get("note") or "")  # on the fill session's heartbeat
    assert "multi_day_error" not in kinds and "multi_day_ca_unread" not in kinds
