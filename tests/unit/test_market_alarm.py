"""The market-event alarm (S103, #377): the pure flag function, its loaders, the report's arithmetic, and the
engine's log line. Pre-registration: docs/prereg/market_alarm_study.md. No network, no real data folder."""

from __future__ import annotations

import ast
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from kaibot.features import market_alarm as ma
from kaibot.ingest import nyse_sessions as nyse

ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("market_alarm_report", ROOT / "scripts" / "market_alarm_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load_script()


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


def committed_inputs() -> ma.Inputs:
    events, covers, missing = ma.calendar_events()
    assert missing == []
    return ma.Inputs(tuple(events), (), (), ("reports", "rankings"), covers)


# ---------------------------------------------------------------- the committed calendars
def test_19_nov_2026_is_major_for_the_ant_listing_from_inputs_dated_18_nov():
    a = ma.alarm(date(2026, 11, 19), ma.as_of_for(date(2026, 11, 19)), committed_inputs())
    assert a["flags"]["mega_listing"] is True and a["tier"] == "major"
    assert a["details"]["mega_listing"] == ["ANT"]
    # the day before the pricing it was not known
    early = ma.alarm(date(2026, 11, 19), ts("2026-11-17T12:00:00-05:00"), committed_inputs())
    assert early["flags"]["mega_listing"] is False


def test_known_dates_give_their_flags():
    inp = committed_inputs()
    f = lambda d: ma.alarm(d, ma.as_of_for(d), inp)["flags"]  # noqa: E731
    assert f(date(2026, 10, 28))["fed_day"] is True
    assert f(date(2016, 1, 20))["cpi_day"] is True  # December 2015 CPI
    assert f(date(2025, 1, 15))["cpi_day"] is True  # from macro_calendar.yaml
    assert f(date(2026, 6, 18))["index_reshuffle"] is True  # Juneteenth: the Thursday
    assert f(date(2026, 6, 19))["index_reshuffle"] is False
    assert f(date(2026, 12, 11))["index_reshuffle"] is True  # the first December Russell reconstitution
    assert f(date(2026, 11, 18)) == {"fed_day": False, "cpi_day": False, "megacap_report": None,
                                     "index_reshuffle": False, "mega_listing": False}


def test_every_index_date_is_a_session_and_follows_its_rule():
    ev = yaml.safe_load((ROOT / "config" / "market_events.yaml").read_text())
    for name, days in ev["index_reshuffle"].items():
        for d in days:
            assert nyse.is_session(d), (name, d)
            assert d.weekday() in (3, 4), (name, d)  # a Friday, or the Thursday before a Friday holiday
    for d in ev["index_reshuffle"]["sp500_quarterly"]:
        assert d.month in (3, 6, 9, 12) and 14 <= d.day <= 21
    for d in ev["index_reshuffle"]["nasdaq100_annual"]:
        assert d.month == 12 and 15 <= d.day <= 21
    assert len(ev["cpi_0830_history"]) == 9 * 12
    # BLS published on two Good Fridays, when the market was closed: no session gets those flags
    assert [d for d in ev["cpi_0830_history"] if not nyse.is_session(d)] == [date(2017, 4, 14), date(2020, 4, 10)]


def test_a_list_past_its_covers_until_is_unknown_not_false():
    inp = committed_inputs()
    a = ma.alarm(date(2027, 6, 25), ma.as_of_for(date(2027, 6, 25)), inp)
    assert a["flags"]["index_reshuffle"] is None and "russell_recon" in a["stale"]
    sp = ma.alarm(date(2027, 3, 19), ma.as_of_for(date(2027, 3, 19)), inp)
    assert sp["flags"]["index_reshuffle"] is True  # a listed date still flags


def test_the_engine_calendar_still_loads_unchanged():
    from kaibot.engine.events import load_calendar

    events, covers = load_calendar(ROOT / "config" / "macro_calendar.yaml")
    assert covers == date(2027, 12, 31) and date(2016, 1, 20) not in events  # history CPI is not a blackout input


# ---------------------------------------------------------------- point in time (property test)
def _random_inputs(rng: random.Random) -> ma.Inputs:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    days = nyse.sessions(date(2024, 1, 2), date(2024, 3, 29))
    kinds = ["fomc", "cpi", "index:sp500_quarterly", "mega_listing", "mega_listing:index_entry"]
    events = tuple(ma.Dated(rng.choice(kinds), rng.choice(days), base + timedelta(hours=rng.randrange(24 * 90)),
                            {"symbol": "X"}) for _ in range(60))
    comps = [f"C{i}" for i in range(15)]
    reports = tuple(ma.Report(rng.choice(comps), "S", rng.choice(days), base + timedelta(hours=rng.randrange(24 * 90)))
                    for _ in range(80))
    rankings = tuple(ma.Ranking(date(2024, m, 1), base + timedelta(days=rng.randrange(90)),
                                frozenset(rng.sample(comps, 10))) for m in (1, 2, 3) for _ in range(2))
    return ma.Inputs(events, reports, rankings, (), {})


def test_deleting_every_later_input_never_changes_the_answer():
    rng = random.Random(377)
    for _ in range(40):
        inp = _random_inputs(rng)
        session = rng.choice(nyse.sessions(date(2024, 1, 2), date(2024, 3, 29)))
        as_of = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=rng.randrange(24 * 90))
        cut = ma.Inputs(tuple(e for e in inp.events if e.available_ts <= as_of),
                        tuple(r for r in inp.reports if r.available_ts <= as_of),
                        tuple(r for r in inp.rankings if r.available_ts <= as_of), (), {})
        assert ma.alarm(session, as_of, inp) == ma.alarm(session, as_of, cut)


def test_megacap_counts_only_the_ten_largest_and_reports_known_by_as_of():
    s = date(2024, 2, 1)
    rank = ma.Ranking(date(2024, 2, 1), ts("2024-01-31T21:15:00+00:00"), frozenset({"A", "B", "C", "D"}),
                      {"A": "AAA", "B": "BBB", "C": "CCC", "D": "DDD"})
    reps = (ma.Report("A", "AAA", s, ts("2024-01-31T21:05:00+00:00")),
            ma.Report("B", "BBB", s, ts("2024-02-01T12:00:00+00:00")),  # 07:00 ET: before 09:00
            ma.Report("C", "CCC", s, ts("2024-02-01T15:00:00+00:00")),  # during the session: after as_of
            ma.Report("Z", "ZZZ", s, ts("2024-01-31T21:05:00+00:00")))  # not in the ten
    a = ma.alarm(s, ma.as_of_for(s), ma.Inputs((), reps, (rank,), (), {}))
    assert a["n_megacaps"] == 2 and a["flags"]["megacap_report"] is True and a["tier"] == "notice"
    assert a["details"]["megacaps_reporting"] == ["AAA", "BBB"]
    three = (*reps, ma.Report("D", "DDD", s, ts("2024-02-01T11:00:00+00:00")))
    assert ma.alarm(s, ma.as_of_for(s), ma.Inputs((), three, (rank,), (), {}))["tier"] == "major"
    assert ma.alarm(s, ma.as_of_for(s), ma.Inputs((), three, (rank,), (), {}), megacap_major=4)["tier"] == "notice"


def test_missing_inputs_are_unknown_never_false():
    a = ma.alarm(date(2024, 2, 1), ma.as_of_for(date(2024, 2, 1)),
                 ma.Inputs((), (), (), ("reports", "macro_calendar", "market_events"), {}))
    assert all(v is None for v in a["flags"].values()) and a["tier"] == "none"


def test_the_alarm_function_does_no_io():
    tree = ast.parse((ROOT / "src" / "kaibot" / "features" / "market_alarm.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "alarm")
    src = ast.unparse(fn)
    for banned in ("open(", "read_", "Path(", "yaml", "datetime.now", "requests", "http"):
        assert banned not in src, banned


def test_nothing_that_trades_imports_the_alarm():
    """It gates nothing: only the engine's entry point (to log it) and the report script may import it."""
    src = ROOT / "src" / "kaibot"
    offenders = []
    for p in src.rglob("*.py"):
        if p.name == "market_alarm.py" or p == src / "engine" / "__main__.py":
            continue
        if "market_alarm import" in p.read_text() or "features.market_alarm" in p.read_text():
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


# ---------------------------------------------------------------- loaders
def test_reports_and_rankings_load_from_their_files(tmp_path):
    (tmp_path / "earnings").mkdir()
    pd.DataFrame({"company": ["A", "A"], "symbol": ["AAA", "AAA"],
                  "session": [pd.Timestamp("2024-02-01"), pd.Timestamp("2024-02-15")],
                  "accepted_ts": [pd.Timestamp("2024-01-31T21:05Z"), pd.Timestamp("2024-02-14T21:05Z")],
                  "report": [True, False]}).to_parquet(tmp_path / "earnings" / "announcements.parquet")
    (tmp_path / "alarm").mkdir()
    pd.DataFrame({"month": ["2024-02-01"], "available_ts": ["2024-01-31T21:15:00+00:00"], "rank": [1],
                  "company": ["A"], "symbol": ["AAA"]}).to_csv(tmp_path / "alarm" / "top10.csv", index=False)
    inp = ma.load_inputs(tmp_path)
    assert inp.missing == () and len(inp.reports) == 1 and inp.rankings[0].companies == {"A"}
    a = ma.alarm(date(2024, 2, 1), ma.as_of_for(date(2024, 2, 1)), inp)
    assert a["flags"]["megacap_report"] is True and a["details"]["megacaps_reporting"] == ["AAA"]


def test_live_alarm_never_reads_past_9_et_and_drops_a_stale_ranking(tmp_path):
    (tmp_path / "alarm").mkdir()
    pd.DataFrame({"month": ["2024-02-01"], "available_ts": ["2024-01-31T21:15:00+00:00"], "rank": [1],
                  "company": ["A"], "symbol": ["AAA"]}).to_csv(tmp_path / "alarm" / "top10.csv", index=False)
    a = ma.live_alarm(date(2024, 2, 1), ts("2024-02-01T15:00:00-05:00"), tmp_path)
    assert a["as_of"] == ma.as_of_for(date(2024, 2, 1)).isoformat()
    late = ma.live_alarm(date(2024, 4, 1), ts("2024-04-01T08:00:00-04:00"), tmp_path)
    assert late["flags"]["megacap_report"] is None and "rankings_stale" in late["missing"]


# ---------------------------------------------------------------- the report's arithmetic
def test_newey_west_without_autocorrelation_is_close_to_ols():
    rng = np.random.default_rng(1)
    flag = (rng.random(4000) < 0.1).astype(float)
    y = rng.normal(0, 1, 4000) + 0.5 * flag
    st = R.newey_west_diff(y, flag)
    assert abs(st["b"] - 0.5) < 0.15 and st["t"] > 5
    ols_se = np.sqrt(np.var(y[flag == 0], ddof=1) * (1 / flag.sum() + 1 / (len(y) - flag.sum())))
    assert abs(st["se"] / ols_se - 1) < 0.15
    assert R.newey_west_diff(np.array([1.0, 2.0, np.nan]), np.array([1.0, 0.0, 0.0]))["t"] is None


def test_rankings_take_the_ten_highest_dollar_volumes_of_members_one_ticker_per_company():
    dates = np.array(pd.bdate_range("2024-01-01", "2024-03-29").values.astype("datetime64[D]"))
    n, k = len(dates), 14
    close = np.full((n, k), 10.0)
    vol = np.tile(np.arange(1, k + 1, dtype=float), (n, 1)) * 1e6
    member = np.ones((n, k), dtype=bool)
    member[:, 13] = False  # the biggest is not a member
    company = {j: f"C{j}" for j in range(k)}
    company[12] = "C11"  # two tickers of one company: the larger counts once
    t = R.rankings_from_panel(dates, [f"S{j}" for j in range(k)], close, vol, member, company,
                              date(2024, 1, 1), date(2024, 4, 30))
    feb = t[t["month"] == "2024-03-01"]
    assert list(feb["company"]) == ["C11", "C10", "C9", "C8", "C7", "C6", "C5", "C4", "C3", "C2"]
    assert feb.iloc[0]["symbol"] == "S12" and feb.iloc[0]["ranked_on"] == "2024-02-29"
    assert t["month"].min() == "2024-03-01"  # at the end of January there are fewer than 40 sessions: no ranking


def test_tightening_raises_the_threshold_until_major_is_at_most_one_in_ten():
    days = nyse.sessions(date(2024, 1, 2), date(2024, 1, 31))
    rank = ma.Ranking(date(2024, 1, 1), ts("2023-12-29T21:15:00+00:00"), frozenset(f"C{i}" for i in range(10)))
    reps = tuple(ma.Report(f"C{i}", "S", d, ts(f"{d}T12:00:00+00:00")) for d in days for i in range(3))
    thr, rows, steps = R.tighten(days, ma.Inputs((), reps, (rank,), (), {}))
    assert [s["megacap_major"] for s in steps] == [3, 4] and thr == 4
    assert all(r["tier"] == "notice" for r in rows)


def test_flags_only_run_reads_no_outcome_and_writes_the_report(tmp_path, capsys):
    assert R.main(["--start", "2026-11-02", "--end", "2026-11-20", "--flags-only", "--data", str(tmp_path)]) == 0
    md = (tmp_path / "reports" / "market_alarm_flags.md").read_text()
    assert "tier **major**" in md and "Comparisons not run" in md
    assert not (tmp_path / "trials.jsonl").exists()
    rep = json.loads((tmp_path / "reports" / "market_alarm_flags.json").read_text())
    assert rep["check"]["details"]["mega_listing"] == ["ANT"]


def test_a_comparison_is_logged_once(tmp_path):
    f = tmp_path / "trials.jsonl"
    f.write_text(json.dumps({"strategy": R.STUDY, "trial_id": f"{R.STUDY}_fed_day_M1", "n_trials": 1}) + "\n")
    assert R.logged_ids(f) == {f"{R.STUDY}_fed_day_M1"}


# ---------------------------------------------------------------- the engine's log line
def test_the_engine_flag_is_off_by_default():
    from kaibot.engine.__main__ import market_alarm_from_env

    assert market_alarm_from_env({}) is None
    assert market_alarm_from_env({"KAIBOT_MARKET_ALARM": "on"}) is ma.live_alarm


class _Store:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    def event(self, kind, payload, rid=None):
        self.events.append((kind, payload))


class _Eng:
    from kaibot.engine.core import Engine

    _log_market_alarm = Engine._log_market_alarm

    def __init__(self, fn):
        self.market_alarm, self.store = fn, _Store()
        self.now = lambda: datetime(2026, 11, 19, 13, 0, tzinfo=UTC)

    def _rid(self):
        return "r1"


def test_the_engine_writes_one_event_and_survives_a_failing_alarm():
    e = _Eng(lambda s, now: {"session": str(s), "tier": "major"})
    e._log_market_alarm(date(2026, 11, 19))
    assert e.store.events == [("market_alarm", {"session": "2026-11-19", "tier": "major"})]

    def boom(s, now):
        raise RuntimeError("no file")

    b = _Eng(boom)
    b._log_market_alarm(date(2026, 11, 19))
    assert b.store.events[0][0] == "market_alarm_error"
    n = _Eng(None)
    n._log_market_alarm(date(2026, 11, 19))
    assert n.store.events == []


def test_a_replay_with_the_alarm_places_the_same_orders(tmp_path):
    """Behaviour-neutral: the same engine replay with and without the alarm sends the same orders."""
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    import spine_fixture as sf
    from test_engine_replay_parity import engine_run

    fx = sf.load()
    days = fx.test_days[:3]
    calls = []

    def alarm_fn(s, now):
        calls.append(s)
        return ma.alarm(s, now, committed_inputs())

    plain = engine_run(tmp_path / "a", fx, days)
    with_alarm = engine_run(tmp_path / "b", fx, days, market_alarm=alarm_fn)
    # ids come from the store's shared row counter, which the extra event rows advance: everything else is equal
    def strip(rows):
        drop = ("id", "run_id", "created_at", "signal_id", "client_order_id")
        return [{k: v for k, v in r.items() if k not in drop} for r in rows]

    assert strip(plain.store.tables.get("order", [])) == strip(with_alarm.store.tables.get("order", []))
    assert [t.key for t in plain.trades] == [t.key for t in with_alarm.trades]
    assert len(calls) == len(days)
    assert sum(1 for r in with_alarm.store.tables.get("event", []) if r.get("type") == "market_alarm") == len(days)
