"""8-K filing flags (S101, #375): the shadow observer and the pre-registered study's arithmetic.

No real filings file and no network. Pre-registration: docs/prereg/filing_flags_study.md.
"""

from __future__ import annotations

import importlib.util
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.features import filing_flag as ff

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("filing_flags_study", ROOT / "scripts" / "filing_flags_study.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
CFG = ff.load_config()


def F(sym, day0, acc, items, company=None):
    return ff.Filing(company or sym, sym, day0, datetime.fromisoformat(acc).astimezone(UTC), ff.split_items(items))


# ---------------------------------------------------------------- config and groups
def test_the_config_holds_exactly_the_pre_registered_groups_and_nothing_counted_yet():
    assert CFG["groups"] == {"g402_non_reliance": ["4.02"], "g401_auditor": ["4.01"],
                             "g205_206_restructuring": ["2.05", "2.06"], "g502_officers": ["5.02"],
                             "g101_agreement": ["1.01"], "g301_103_delisting_bankruptcy": ["3.01", "1.03"]}
    assert CFG["counted"] == {} and CFG["exclude_items"] == ["2.02"]
    prereg = (ROOT / "docs" / "prereg" / "filing_flags_study.md").read_text()
    for g in CFG["groups"]:
        assert f"`{g}`" in prereg


def test_groups_of_excludes_earnings_filings():
    assert ff.groups_of(("5.02", "9.01"), CFG) == ["g502_officers"]
    assert ff.groups_of(("2.02", "2.05"), CFG) == []
    assert ff.groups_of(("2.06", "1.01"), CFG) == ["g205_206_restructuring", "g101_agreement"]
    assert ff.groups_of(("12.02",), CFG) == []


# ---------------------------------------------------------------- the observer
def test_flags_read_only_filings_known_at_as_of_and_inside_the_window():
    rows = [F("AAA", date(2024, 3, 4), "2024-03-01T21:30:00+00:00", "4.02,9.01"),  # Fri after close -> Mon
            F("AAA", date(2024, 3, 12), "2024-03-11T21:30:00+00:00", "5.02"),  # after the decision's as_of
            F("BBB", date(2024, 1, 2), "2023-12-29T21:30:00+00:00", "1.01"),  # too old for 20 sessions
            F("CCC", date(2024, 3, 4), "2024-03-01T21:30:00+00:00", "2.02,2.05")]  # earnings: excluded
    as_of = datetime(2024, 3, 8, 22, 0, tzinfo=UTC)
    out = ff.flags(["AAA", "BBB", "CCC", "DDD"], as_of, date(2024, 3, 8), rows, CFG)
    assert list(out) == ["AAA"]
    assert out["AAA"] == [{"group": "g402_non_reliance", "day0": "2024-03-04",
                           "accepted_ts": "2024-03-01T21:30:00+00:00", "sessions_since": 4, "sessions": 20,
                           "counted": False}]


def test_a_counted_group_uses_its_own_number_of_sessions():
    cfg = {**CFG, "counted": {"g402_non_reliance": 5}}
    rows = [F("AAA", date(2024, 3, 4), "2024-03-01T21:30:00+00:00", "4.02")]
    as_of = datetime(2024, 3, 30, tzinfo=UTC)
    assert ff.flags(["AAA"], as_of, date(2024, 3, 11), rows, cfg)["AAA"][0]["counted"] is True  # 5 sessions
    assert ff.flags(["AAA"], as_of, date(2024, 3, 12), rows, cfg) == {}  # 6 sessions


def test_deleting_later_filings_never_changes_the_flags():
    rng = random.Random(375)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    items = ["4.02", "4.01", "2.05", "5.02", "1.01", "3.01", "2.02", "8.01"]
    for _ in range(30):
        rows = []
        for _ in range(60):
            acc = base + timedelta(hours=rng.randrange(24 * 80))
            day0 = acc.date() + timedelta(days=1)
            rows.append(ff.Filing("C", rng.choice("ABCD"), day0, acc, (rng.choice(items),)))
        as_of = base + timedelta(hours=rng.randrange(24 * 80))
        session = as_of.date()
        cut = [r for r in rows if r.accepted_ts <= as_of]
        assert ff.flags(list("ABCD"), as_of, session, rows, CFG) == ff.flags(list("ABCD"), as_of, session, cut, CFG)


def test_the_observer_is_off_by_default_and_never_raises(tmp_path):
    assert ff.from_env({}) is None and ff.from_env({"KAIBOT_FILING_FLAG": "on"}) is None
    assert isinstance(ff.from_env({"KAIBOT_FILING_FLAG": "shadow"}), ff.ShadowFilingFlags)
    obs = ff.ShadowFilingFlags(path=tmp_path / "missing.parquet")
    out = obs(["AAA"], datetime(2024, 3, 8, tzinfo=UTC), date(2024, 3, 8))
    assert out["flags"] == {} and "error" in out


def test_the_observer_reads_the_research_file(tmp_path):
    p = tmp_path / "filings_8k.parquet"
    pd.DataFrame({"company": ["A", "A"], "symbol": ["AAA", "AAA"],
                  "session": [pd.Timestamp("2024-03-04"), pd.Timestamp("2024-03-04")],
                  "accepted_ts": [pd.Timestamp("2024-03-01T21:30Z"), pd.Timestamp("2024-03-01T21:40Z")],
                  "form": ["8-K", "8-K/A"], "items": ["4.01", "4.02"]}).to_parquet(p)
    out = ff.ShadowFilingFlags(path=p)(["AAA"], datetime(2024, 3, 8, 22, tzinfo=UTC), date(2024, 3, 8))
    assert [f["group"] for f in out["flags"]["AAA"]] == ["g401_auditor"]  # the amendment is left out
    assert out["file_last_accepted"] == "2024-03-01T21:30:00+00:00" and out["counted_groups"] == []


def test_the_engine_only_hands_the_observer_on_and_never_names_the_research_store():
    from kaibot.engine.__main__ import filing_flags_from_env

    assert filing_flags_from_env({}) is None
    assert isinstance(filing_flags_from_env({"KAIBOT_FILING_FLAG": "shadow"}), ff.ShadowFilingFlags)
    for f in (ROOT / "src" / "kaibot" / "engine").rglob("*.py"):
        text = f.read_text()
        assert "filings_8k" not in text and "filings_pit" not in text, f
        if f.name != "__main__.py":
            assert "filing_flag import" not in text and "features.filing_flag" not in text, f


class _Rec:
    def __init__(self):
        self.events = []

    def __call__(self, kind, body):
        self.events.append((kind, body))


def test_the_daily_path_logs_one_event_and_survives_a_failing_observer():
    from kaibot.engine.live_daily import LiveDaily

    class Stub:
        _filing_observe = LiveDaily._filing_observe

    s = Stub()
    s.event = _Rec()
    s.filing_flags = lambda syms, now, session: {"flags": {"AAA": []}, "symbols_checked": len(syms)}
    s._filing_observe("trend_momentum", date(2024, 3, 8), datetime(2024, 3, 8, 22, tzinfo=UTC), ["BBB", "AAA", "AAA"])
    assert s.event.events == [("filing_flag_shadow", {"strategy": "trend_momentum", "session": "2024-03-08",
                                                      "flags": {"AAA": []}, "symbols_checked": 2})]

    def boom(*a):
        raise RuntimeError("x")

    s.event, s.filing_flags = _Rec(), boom
    s._filing_observe("trend_momentum", date(2024, 3, 8), datetime(2024, 3, 8, 22, tzinfo=UTC), ["AAA"])
    assert s.event.events == []
    s.filing_flags = None
    s._filing_observe("trend_momentum", date(2024, 3, 8), datetime(2024, 3, 8, 22, tzinfo=UTC), ["AAA"])
    assert s.event.events == []


# ---------------------------------------------------------------- the study's arithmetic
def test_events_keep_one_filing_per_company_month_members_only_and_no_earnings():
    t = pd.DataFrame({
        "company": ["A", "A", "A", "B", "C", "A"],
        "symbol": ["AAA", "AAA", "AAA", "BBB", "CCC", "AAA"],
        "session": pd.to_datetime(["2024-03-04", "2024-03-20", "2024-04-02", "2024-03-05", "2024-03-05",
                                   "2024-03-06"]),
        "accepted_ts": pd.to_datetime(["2024-03-01T21:00Z", "2024-03-19T21:00Z", "2024-04-01T21:00Z",
                                       "2024-03-04T21:00Z", "2024-03-04T21:00Z", "2024-03-05T21:00Z"]),
        "form": ["8-K", "8-K", "8-K", "8-K", "8-K", "8-K/A"],
        "items": ["5.02", "5.02,9.01", "5.02", "5.02,2.02", "5.02", "5.02"]})
    ev, left = S.events(t, CFG, date(2017, 5, 1), lambda c, d: c != "C")
    g = ev[ev["group"] == "g502_officers"]
    assert list(zip(g["company"], g["day0"].astype(str), strict=True)) == [("A", "2024-03-04"), ("A", "2024-04-02")]
    assert left["amendments"] == 1 and left["with_2_02"] == 1


def test_window_return_uses_the_last_close_inside_the_window():
    c = np.array([100.0, 101.0, np.nan, 110.0, np.nan, np.nan])
    assert S.window_return(c, 0, 3) == pytest.approx(0.1)
    assert S.window_return(c, 0, 5) == pytest.approx(0.1)  # delisted after day 3
    assert S.window_return(c, 2, 2) is None  # no close on day 0
    assert S.window_return(c, 4, 1) is None


def test_judge_needs_30_obs_t_35_on_the_smaller_t_and_6_years():
    rng = np.random.default_rng(3)
    rows = [{"ar": -0.02 + rng.normal(0, 0.01), "month": date(y, m, 1), "year": y}
            for y in range(2017, 2027) for m in (1, 4, 7, 10)]
    st = S.judge(pd.DataFrame(rows))
    assert st["n"] == 40 and st["counts"] and st["negative"] and st["same_sign_years"] == 10
    assert abs(st["t"]) == min(abs(st["t_obs"]), abs(st["t_month"]))
    few = S.judge(pd.DataFrame(rows[:29]))
    assert not few["counts"]
    four_years = S.judge(pd.DataFrame([r for r in rows if r["year"] < 2021] * 2))
    assert four_years["same_sign_years"] == 4 and not four_years["counts"]


def test_only_negative_groups_count_and_w20_wins():
    yes_neg, yes_pos, no = {"counts": True, "negative": True}, {"counts": True, "negative": False}, {"counts": False,
                                                                                                    "negative": True}
    cells = {("g402_non_reliance", "w5"): yes_neg, ("g402_non_reliance", "w20"): yes_neg,
             ("g401_auditor", "w5"): yes_neg, ("g401_auditor", "w20"): no,
             ("g101_agreement", "w5"): yes_pos, ("g101_agreement", "w20"): yes_pos}
    assert S.verdict(cells) == {"g402_non_reliance": 20, "g401_auditor": 5}


def test_the_study_refuses_to_run_without_the_filings_file(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        S.main(["--data", str(tmp_path)])
    assert "fetch_earnings_dates.sh --all-items" in str(e.value)


def _bars(root: Path, key: str, days: list[date], closes: np.ndarray) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    p = root / "bars" / f"symbol={key}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"date": pa.array(days, pa.date32()), "o": closes, "h": closes, "l": closes,
                             "c": closes, "v": np.full(len(days), 1e6)}), p)


def test_the_study_runs_end_to_end_on_a_tiny_store_and_logs_twelve_rows_once(tmp_path):
    from kaibot.ingest import nyse_sessions as nyse

    days = nyse.sessions(date(2024, 1, 2), date(2024, 6, 28))
    n = len(days)
    _bars(tmp_path / "daily", "SPY", days, np.linspace(400, 420, n))
    _bars(tmp_path / "daily", "AAPL", days, np.linspace(180, 160, n))  # falls against SPY
    import pyarrow.parquet as pq

    empty = pq.read_table(ROOT / "tests" / "fixtures" / "daily_momentum" / "corporate_actions.parquet").slice(0, 0)
    pq.write_table(empty, tmp_path / "daily" / "corporate_actions.parquet")  # no splits, no dividends
    (tmp_path / "filings").mkdir()
    pd.DataFrame({"company": ["AAPL"] * 3, "symbol": ["AAPL"] * 3,
                  "session": pd.to_datetime(["2024-02-01", "2024-03-01", "2024-03-15"]),
                  "accepted_ts": pd.to_datetime(["2024-01-31T21:00Z", "2024-02-29T21:00Z", "2024-03-14T21:00Z"]),
                  "form": ["8-K"] * 3, "items": ["4.02", "4.02", "4.02"]}).to_parquet(
        tmp_path / "filings" / "filings_8k.parquet")
    assert S.main(["--start", "2024-01-01", "--data", str(tmp_path)]) == 0
    md = (tmp_path / "reports" / "filing_flags_study.md").read_text()
    assert md.count("| `g") == 12 and "none: the idea is dropped" in md
    rows = [json.loads(x) for x in (tmp_path / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 12 and all(r["strategy"] == "filing_flags_study" and r["n_trials"] == 1 for r in rows)
    cell = next(r for r in rows if r["trial_id"] == "filing_flags_study_g402_non_reliance_w5")
    assert cell["metrics"]["n"] == 2 and cell["metrics"]["mean"] < 0  # one per company and month: 2 of 3
    assert S.main(["--start", "2024-01-01", "--data", str(tmp_path)]) == 0
    assert len((tmp_path / "trials.jsonl").read_text().splitlines()) == 12  # never logged twice
