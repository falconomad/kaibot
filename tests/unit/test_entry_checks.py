"""M12 ticket 3.11 (#473), the daily half: deterministic checks on the daily store that block NEW entries
(kaibot.ingest.entry_checks), their wiring in the live daily path (buys only; sales, stops and cancels never ask),
and the deadline of the wait for an evening's daily prices (`multi_day_data_late`).

Fault seeds: each kind of bad daily data the ticket names (SPY's bar missing after the deadline, a stock's bars
stopping early, a zero or negative price, a split the corporate-actions table lacks, a move far beyond the stock's
range with no corporate action, a change log that has not been updated) triggers its block, and the clean case does
not. Every block clears by itself once the data is right."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.common import switches
from kaibot.engine import live_daily as ld
from kaibot.ingest import entry_checks as ec
from kaibot.ingest.alpaca_history import ET
from kaibot.ingest.daily_bars import ACTIONS_SCHEMA, BARS_SCHEMA, actions_path, bars_path

# ---------------------------------------------------------------- synthetic bars

DAYS = pd.bdate_range("2026-06-01", "2026-11-24")  # weekdays (holidays do not matter to the checks)


def _bars(closes, days=DAYS) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    d = days[: len(c)]
    return pd.DataFrame({"date": d.date, "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 1e6})


def _walk(n=None, seed=0, vol=0.01, start=100.0):
    r = np.random.default_rng(seed).normal(0, vol, len(DAYS) if n is None else n)
    return start * np.exp(np.cumsum(r))


def _actions(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["key", "type", "ex_date"])


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, ET).astimezone(UTC)


def _prev(d: date) -> date:  # weekdays as sessions, 27 Nov 2026 (Thanksgiving) a holiday
    x = d - timedelta(days=1)
    while x.weekday() >= 5 or x == date(2026, 11, 26):
        x -= timedelta(days=1)
    return x


# ---------------------------------------------------------------- the registry


def test_the_reason_codes_are_one_registry_and_the_switch_offers_exactly_them():
    assert set(ec.CHOICES) | {"checks_failed"} == set(ec.REASONS)
    assert switches.SWITCHES["KAIBOT_DAILY_CHECKS_BLOCK"].choices == ec.CHOICES
    # off by default: nothing blocks; "on" is every check but price_jump (a real news move looks the same)
    assert switches.value("KAIBOT_DAILY_CHECKS_BLOCK", {}) == ()
    assert set(switches.value("KAIBOT_DAILY_CHECKS_BLOCK", {"KAIBOT_DAILY_CHECKS_BLOCK": "on"})) == \
        set(ec.CHOICES) - {"price_jump"}
    with pytest.raises(switches.SwitchError):
        switches.value("KAIBOT_DAILY_CHECKS_BLOCK", {"KAIBOT_DAILY_CHECKS_BLOCK": "stale"})
    b = ec.Block("benchmark_bar_missing", "no daily bar for 2026-11-24", "SPY")
    assert b.text().startswith("data check benchmark_bar_missing SPY: ")
    assert ec.parse(b.text()) == "benchmark_bar_missing"
    assert ec.parse(ec.Block("universe_stale", "old").text()) == "universe_stale"
    assert ec.parse("lease unverified") is None and ec.parse(None) is None
    with pytest.raises(ValueError):
        ec.Block("not_a_code", "x")


# ---------------------------------------------------------------- the deadline


def test_the_deadline_is_0330_et_the_night_after_the_session_and_the_expected_session_follows_it():
    assert ec.data_deadline(date(2026, 11, 24)) == datetime(2026, 11, 25, 3, 30, tzinfo=ET)
    # 24 Nov 2026 (today's case): at 21:00 ET the bar of the 24th is not due yet, the 23rd's is
    assert ec.expected_session(_at(date(2026, 11, 24), time(21, 0)), _prev) == date(2026, 11, 23)
    assert ec.expected_session(_at(date(2026, 11, 25), time(3, 29)), _prev) == date(2026, 11, 23)
    assert ec.expected_session(_at(date(2026, 11, 25), time(3, 30)), _prev) == date(2026, 11, 24)
    # over a weekend and a holiday: Friday's bar is due Saturday 03:30; on Thanksgiving Friday, Wednesday's
    assert ec.expected_session(_at(date(2026, 11, 23), time(2, 0)), _prev) == date(2026, 11, 20)
    assert ec.expected_session(_at(date(2026, 11, 27), time(9, 0)), _prev) == date(2026, 11, 25)


# ---------------------------------------------------------------- the market checks


def _market(spy, expected=date(2026, 11, 24), age=(30, date(2026, 10, 25))):
    return [b.code for b in ec.market_findings(spy, expected, today=date(2026, 11, 25), changes_age=age)]


def test_spy_without_the_due_bar_blocks_and_the_bar_arriving_clears_it():
    spy = _bars(_walk())
    assert _market(spy) == []
    assert _market(spy[spy["date"] < date(2026, 11, 24)]) == ["benchmark_bar_missing"]
    assert _market(None) == ["benchmark_bar_missing"]
    assert _market(spy.iloc[:0]) == ["benchmark_bar_missing"]


@pytest.mark.parametrize("bad", [0.0, -1.0, np.nan])
def test_a_zero_negative_or_missing_spy_price_in_the_newest_bars_blocks(bad):
    spy = _bars(_walk())
    spy.loc[len(spy) - 2, "l"] = bad
    assert _market(spy) == ["price_not_positive"]
    old = _bars(_walk())
    old.loc[10, "c"] = bad  # outside the window: the integrity gate's business (daily_integrity), not this one
    assert _market(old) == []


def test_a_change_log_without_a_change_for_120_days_blocks(tmp_path):
    f = tmp_path / "changes.csv"
    f.write_text("# header\ndate,added,removed,source\n2016-01-04,A,,x\n2026-07-01,B,C,x\n")
    assert ec.universe_age(f, date(2026, 11, 25)) == (147, date(2026, 7, 1))
    assert _market(_bars(_walk()), age=ec.universe_age(f, date(2026, 11, 25))) == ["universe_stale"]
    assert _market(_bars(_walk()), age=ec.universe_age(f, date(2026, 10, 25))) == []
    from kaibot.ingest.sp500_pit import CHANGES

    days, _ = ec.universe_age(CHANGES, date(2026, 11, 25))
    assert days <= ec.UNIVERSE_MAX_AGE_DAYS  # the committed log passes today


# ---------------------------------------------------------------- the stock checks


def _stock(bars, spy=None, actions=None):
    return [b.code for b in ec.stock_findings("AAA", bars, _bars(_walk(seed=9)) if spy is None else spy, actions)]


def test_a_clean_stock_passes():
    assert _stock(_bars(_walk(seed=1))) == []


def test_a_stock_whose_bars_stop_before_spys_blocks():
    b = _bars(_walk(seed=1))
    assert _stock(b.iloc[:-1]) == ["daily_bar_stale"]
    assert _stock(None) == ["daily_bar_stale"]


def test_a_zero_price_in_the_stocks_newest_bars_blocks():
    b = _bars(_walk(seed=1))
    b.loc[len(b) - 1, "o"] = 0.0
    assert "price_not_positive" in _stock(b)


@pytest.mark.parametrize("ratio", [0.5, 1 / 3, 0.25, 0.1, 10.0])
def test_a_split_sized_step_without_its_action_blocks_and_the_action_clears_it(ratio):
    c = _walk(seed=2)
    k = len(c) - 20
    c[k:] *= ratio  # raw bars: a split on day k
    b = _bars(c)
    assert _stock(b) == ["split_not_applied"]  # and not a price_jump as well
    kind = "forward_split" if ratio < 1 else "reverse_split"
    assert _stock(b, actions=_actions([["AAA", kind, b["date"][k]]])) == []
    # a split older than SPLIT_WINDOW sessions is no longer looked for
    c2 = _walk(seed=2)
    c2[len(c2) - ec.SPLIT_WINDOW - 5:] *= ratio
    assert _stock(_bars(c2)) == []


def test_a_move_far_beyond_the_stocks_range_with_no_action_is_a_price_jump():
    c = _walk(seed=3)
    c[-2:] *= 1.35
    b = _bars(c)
    assert _stock(b) == ["price_jump"]
    assert _stock(b, actions=_actions([["AAA", "spin_off", b["date"][len(b) - 2]]])) == []
    # SPY moving with it (a market-wide day) is not the stock's jump
    spy = _walk(seed=9)
    spy[-2:] *= 1.35
    assert _stock(b, spy=_bars(spy)) == []
    # five sessions on, the jump has left the window: the block lets go by itself
    c3 = _walk(seed=3)
    c3[-(ec.WINDOW + 2):] *= 1.35
    assert _stock(_bars(c3)) == []


def test_an_ordinary_volatile_stock_never_jumps():
    for seed in range(20):
        assert _stock(_bars(_walk(seed=seed, vol=0.04))) == [], seed


# ---------------------------------------------------------------- on the store


def _write_bars(root, key, df):
    p = bars_path(root, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    t = {"date": pa.array(list(df["date"]), pa.date32())}
    for col in ("o", "h", "l", "c", "v", "vw"):
        t[col] = pa.array(df["c" if col == "vw" else col].astype(float).tolist(), pa.float64())
    t["n"] = pa.array([1] * len(df), pa.int64())
    t["available_ts"] = pa.array([datetime.combine(d, time(20, 15), ET).astimezone(UTC) for d in df["date"]],
                                 pa.timestamp("s", tz="UTC"))
    pq.write_table(pa.table({f.name: t[f.name] for f in BARS_SCHEMA}, schema=BARS_SCHEMA), p)


def _write_actions(root, rows):
    cols = {f.name: [] for f in ACTIONS_SCHEMA}
    for key, kind, ex in rows:
        r = {"key": key, "symbol": key, "type": kind, "ex_date": ex, "ratio": 2.0, "cash": None, "special": False,
             "foreign": False, "process_date": ex, "record_date": ex, "payable_date": ex, "cusip": "", "id": key,
             "dup": False, "raw": "{}"}
        for k, v in r.items():
            cols[k].append(v)
    pq.write_table(pa.table(cols, schema=ACTIONS_SCHEMA), actions_path(root))


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "daily"
    spy = _bars(_walk(seed=9))
    _write_bars(root, "SPY", spy[spy["date"] < date(2026, 11, 24)])  # the nightly refresh has not run yet
    c = _walk(seed=2)
    c[-20:] *= 0.5
    _write_bars(root, "AAA", _bars(c))
    _write_actions(root, [])
    changes = tmp_path / "changes.csv"
    changes.write_text("2026-11-04,OKTA,KVUE,x\n")
    return SimpleNamespace(root=root, spy=spy, aaa=_bars(c), changes=changes)


def test_the_store_checks_block_until_the_refresh_writes_the_bar_and_the_action(store):
    chk = ec.DailyEntryChecks(store.root, frozenset(switches.value("KAIBOT_DAILY_CHECKS_BLOCK",
                                                                   {"KAIBOT_DAILY_CHECKS_BLOCK": "on"})),
                              changes=store.changes)
    now = _at(date(2026, 11, 25), time(9, 0))
    exp = ec.expected_session(now, _prev)
    assert [b.code for b in chk.market_blocks(now, exp)] == ["benchmark_bar_missing"]
    assert [b.code for b in chk.stock_blocks("AAA")] == ["split_not_applied"]
    _write_bars(store.root, "SPY", store.spy)  # ./scripts/fetch_daily.sh ran
    assert chk.market_blocks(now, exp) == []
    _write_actions(store.root, [["AAA", "forward_split", store.aaa["date"][len(store.aaa) - 20]]])
    assert chk.stock_blocks("AAA") == []
    st = chk.status()
    assert st["blocked"] == 0 and "price_jump" not in st["blocking"]


def test_a_check_that_is_not_blocking_is_reported_only(store):
    chk = ec.DailyEntryChecks(store.root, frozenset({"universe_stale"}), changes=store.changes)
    now = _at(date(2026, 11, 25), time(9, 0))
    assert chk.market_blocks(now, ec.expected_session(now, _prev)) == []
    assert chk.status()["market"][0]["code"] == "benchmark_bar_missing" and chk.status()["blocked"] == 0


# ---------------------------------------------------------------- in the live daily path: buys only


class _FakeChecks:
    def __init__(self, market=(), stock=None, boom=False):
        self.market, self.stock, self.boom = list(market), dict(stock or {}), boom

    def market_blocks(self, now, expected):
        if self.boom:
            raise OSError("disk")
        return list(self.market)

    def stock_blocks(self, symbol):
        return list(self.stock.get(symbol, []))

    def status(self):
        return {"blocking": ["benchmark_bar_missing"]}


class _Account:
    def entry_block(self):
        return None

    def run_id(self):
        return 1

    def book_ended(self):
        return False

    def held(self, name):
        return {}

    def allows(self, symbol, side, purpose="target", qty=None):
        return None


def _path(tmp_path, checks, load=None, now=None, events=None):
    from kaibot.engine.journal import Journal

    clock = SimpleNamespace(t=now or _at(date(2026, 11, 24), time(15, 50)))
    j = Journal(tmp_path / "j.sqlite", now=lambda: clock.t)
    p = ld.LiveDaily(None, j, lambda: clock.t, [], load or (lambda k, now: None), _Account(),
                     next_session=lambda d: d + timedelta(days=1), prev_session=_prev,
                     event=lambda kind, body: (events if events is not None else []).append((kind, body)),
                     entry_checks=checks)
    return p, j, clock


def test_a_market_block_stops_every_buy_and_never_a_sale_and_is_journalled_once(tmp_path):
    missing = ec.Block("benchmark_bar_missing", "no daily bar for 2026-11-23", "SPY")
    chk = _FakeChecks(market=[missing])
    ev: list = []
    p, j, _ = _path(tmp_path, chk, events=ev)
    assert p.gate.allows("AAA", "buy").startswith("entries blocked: data check benchmark_bar_missing")
    assert p.gate.allows("AAA", "sell") is None  # sales, exits and stops are never asked about
    assert p.gate.allows("AAA", "buy") is not None
    rows = [b for *_, b in j.entries(kinds=("md_data_block",))]
    assert len(rows) == 1 and rows[0]["code"] == "benchmark_bar_missing" and rows[0]["scope"] == "market"
    assert [k for k, _ in ev] == ["data_entry_block"]
    chk.market = []  # the bar arrived
    assert p.gate.allows("AAA", "buy") is None
    assert [b["codes"] for *_, b in j.entries(kinds=("md_data_unblock",))] == [["benchmark_bar_missing"]]
    assert [k for k, _ in ev] == ["data_entry_block", "data_entry_unblocked"]
    # a restart does not journal the same day's block again
    chk.market = [missing]
    p2, _, _ = _path(tmp_path, chk)
    p2._market_blocked = []
    p2.data_block()
    assert len(list(j.entries(kinds=("md_data_block",)))) == 2  # once more after the clear, not on every call
    p2.data_block()
    assert len(list(j.entries(kinds=("md_data_block",)))) == 2


def test_a_stock_block_stops_buys_of_that_stock_only(tmp_path):
    chk = _FakeChecks(stock={"AAA": [ec.Block("split_not_applied", "ratio 0.5", "AAA")]})
    p, _, _ = _path(tmp_path, chk)
    assert "split_not_applied AAA" in p.gate.allows("AAA", "buy")
    assert p.gate.allows("BBB", "buy") is None
    assert p.gate.allows("AAA", "sell") is None


def test_a_check_that_raises_blocks_buys_fail_closed_and_never_raises(tmp_path):
    p, _, _ = _path(tmp_path, _FakeChecks(boom=True))
    assert ec.parse(p.data_block()) == "checks_failed"
    assert p.gate.allows("AAA", "sell") is None


def test_with_the_checks_off_nothing_is_read_or_blocked(tmp_path):
    p, j, _ = _path(tmp_path, None)
    assert p.data_block() is None and p.data_block("AAA") is None and p.gate.data_block is None
    assert p.gate.allows("AAA", "buy") is None and not list(j.entries(kinds=("md_data_block",)))
    assert "data_checks" not in p.status()


def test_the_engines_entry_block_names_the_market_check():
    from kaibot.engine.core import Engine

    daily = SimpleNamespace(entry_checks=object(), data_block=lambda: "data check benchmark_bar_missing SPY: x")
    eng = SimpleNamespace(day=None, run={"status": "running"}, _cmd_waiting={}, account_clean=True,
                          _reconciled=lambda: True, kill=None, lease_ok=lambda: True, clock_skew=None, daily=daily)
    assert ec.parse(Engine.entry_block(eng)) == "benchmark_bar_missing"
    eng.daily = SimpleNamespace(entry_checks=None, data_block=lambda: "never asked")
    assert Engine.entry_block(eng) is None
    eng.daily = None
    assert Engine.entry_block(eng) is None


def test_on_the_sim_venue_a_market_block_stops_the_buys_from_its_day_and_every_sale_still_goes_out(tmp_path):
    """The whole send step on SimBroker (the parity harness of test_live_daily): from session X on the market check
    fails. No buy reaches the venue from X on; the rule's sales still do. Before X the orders are the unblocked
    run's, order for order."""
    from test_live_daily import I0, I1, Harness, live_book_limits, venue_orders

    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData
    from kaibot.strategies import momentum as mom

    m, _ = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(m)
    s = mom.VERSIONS["trend_momentum"]
    lim = live_book_limits(s)
    free = Harness(data, s, ld.live_builder(s, lim), tmp_path / "free")
    free.run()
    x = data.day(I0 + 40)
    block = ec.Block("benchmark_bar_missing", "seeded", "SPY")

    class FromX:
        def __init__(self, clock):
            self.clock = clock

        def market_blocks(self, now, expected):
            return [block] if self.clock().astimezone(ET).date() >= x else []

        def stock_blocks(self, symbol):
            return []

        def status(self):
            return {}

    (tmp_path / "blocked").mkdir()
    h = Harness(data, s, ld.live_builder(s, lim), tmp_path / "blocked")
    h.live.entry_checks = FromX(h.clock)
    h.live.gate.data_block = lambda sym: h.live.data_block() or h.live.data_block(sym)
    h.run()
    got, want = venue_orders(h.broker), venue_orders(free.broker)
    assert [o for o in got if o[0] < str(x)] == [o for o in want if o[0] < str(x)]
    after = [o for o in got if o[0] >= str(x)]
    assert after and all(o[2] == "sell" for o in after)  # sales go out; not one buy
    assert any(o[2] == "buy" for o in want if o[0] >= str(x))  # (the unblocked run did buy then)
    assert I1 > I0 + 40
    rows = [b for *_, b in h.journal.entries(kinds=("md_data_block",))]
    assert rows and {r["code"] for r in rows} == {"benchmark_bar_missing"}


# ---------------------------------------------------------------- the deadline of the wait


def test_a_wait_past_the_deadline_is_one_alert_and_clears_when_the_prices_arrive(tmp_path, monkeypatch):
    """24 Nov 2026: the store had no SPY bar for the 24th until ./scripts/fetch_daily.sh ran after 20:15 ET. That
    wait is normal until 03:30 ET; past it the engine says so once (ERROR log, `multi_day_data_late`, heartbeat)."""
    k = date(2026, 11, 24)
    ready = {"ok": False}

    def load(day, now):
        if not ready["ok"]:
            raise ld.DataNotReady(f"the daily store has no SPY bar for {day} (run ./scripts/fetch_daily.sh after "
                                  "20:15 ET)")
        raise ld.DataNotReady("stop here")  # the decision itself is not this test's business

    ev: list = []
    p, _, clock = _path(tmp_path, None, load=load, now=_at(k, time(21, 0)), events=ev)
    rule = SimpleNamespace(name="trend_momentum", strategy=SimpleNamespace(decision_day=None))
    monkeypatch.setattr(ld.LiveDaily, "active", lambda self: [rule])
    monkeypatch.setattr(ld.LiveDaily, "decision", lambda self, name, k: None)
    p.after_close(k)
    assert [x for x, _ in ev] == ["multi_day_waiting"] and not p._late  # 21:00 ET: the normal wait
    clock.t = _at(k + timedelta(days=1), time(3, 31))
    p.after_close(k)
    p.after_close(k)
    late = [b for x, b in ev if x == "multi_day_data_late"]
    assert len(late) == 1 and late[0]["session"] == str(k) and late[0]["deadline"].startswith("2026-11-25T03:30")
    st = p.status()
    assert st["data_late"][0]["rules"] == ["trend_momentum"] and "not in by their deadline" in st["note"]
    p._late_done(k, "trend_momentum")  # the rule decides once the prices are in
    assert "data_late" not in p.status()
    # a newer session takes over: an old alert never lingers
    p._late_check(k, "trend_momentum", "x")
    clock.t = _at(k + timedelta(days=1), time(21, 0))
    p.after_close(k + timedelta(days=1))
    assert str(k) not in p._late
