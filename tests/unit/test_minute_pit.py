"""#794 (decision 19 option B): the 15:50 minutes of the whole point-in-time universe.

What must hold:
- every key of the pit view is a target, as the daily store keys it (a current member whose SYMBOL@ASOF names the
  store's build day too), with its member sessions plus a tail after it left; early closes are never asked;
- months the full history (data/bars_1m) holds are never asked, and a written month is never asked again;
- each key is asked as the store asked it (asof, SIP, split-adjusted), nothing after the research window's end;
  a big group of keys with one as-of date goes one request a session, a former member one key-month at a time;
- only 15:45-15:59 ET of full sessions is kept; Alpaca's 4xx for a symbol is a flag (`unknown_symbol`), never a guess;
- failures write nothing and five in a row stop the run; the time budget stops it between units;
- the coverage report counts per year; the 15:50 reader uses the new store where the full history has no month;
- the runner can run it (allow-list, strict flags, heavy) and the queued jobs are valid.
Offline: the HTTP layer (urllib, under kaibot.common.http) is faked, no request leaves the machine.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.common.http import HttpError
from kaibot.common.ratelimit import Budget, BudgetSpec
from kaibot.engine.config import ROOT
from kaibot.ingest import alpaca_history as ah
from kaibot.ingest import minute_pit as mp
from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.alpaca_history import ET
from kaibot.ingest.sp500_pit import Change, Membership
from kaibot.replay import minute_1550 as m1550

sys.path.insert(0, str(ROOT / "scripts"))
import fetch_minute_pit as script  # noqa: E402
import research_runner as rr  # noqa: E402

START, END = date(2018, 1, 2), date(2018, 7, 31)
CURRENT = [f"C{i:02d}" for i in range(1, 13)]  # 12 current members outside the universe: one as-of date
CFG = {"market": "SPY", "sectors": {"XLK": ["AAA"]}}
STORE_ASOF = "2026-11-05"  # the day the daily store was built, minus 7 days


def membership(today=date(2026, 11, 26)) -> Membership:
    changes = [Change(date(2016, 1, 1), ("AAA", "OLD", "GONE", *CURRENT)),
               Change(date(2018, 3, 1), ("NEW",), ("OLD",))]
    return Membership(changes, {}, ["AAA"], today)


def store_keys() -> list[str]:
    return ["AAA", "OLD@2018-02-21", "GONE@2026-11-05", f"NEW@{STORE_ASOF}", *[f"{c}@{STORE_ASOF}" for c in CURRENT],
            "XLK", "SPY"]


def minute_file(path: Path, days, minutes=range(15 * 60 + 45, 16 * 60), px=10.0) -> None:
    ts = [datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=ET).astimezone(UTC) for d in days for m in minutes]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"ts": pa.array(ts, pa.timestamp("s", tz="UTC")),
                             "available_ts": pa.array([t + timedelta(minutes=1) for t in ts],
                                                      pa.timestamp("s", tz="UTC")),
                             "o": [px] * len(ts), "h": [px] * len(ts), "l": [px] * len(ts), "c": [px] * len(ts),
                             "v": [100.0] * len(ts), "n": [1] * len(ts), "vw": [px] * len(ts)}), path)


class _Resp(io.BytesIO):
    status = 200
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAlpaca:
    """Stands in for urllib.request.urlopen: answers /v2/stocks/bars (multi-symbol), two pages for a range."""

    def __init__(self, unknown=("OLD",)):
        self.unknown = set(unknown)
        self.requests: list[dict] = []

    def __call__(self, req, timeout):
        u = urllib.parse.urlsplit(req.full_url)
        q = dict(urllib.parse.parse_qsl(u.query))
        assert u.netloc == "data.alpaca.markets" and u.path == "/v2/stocks/bars"
        self.requests.append(q)
        syms = q["symbols"].split(",")
        if self.unknown & set(syms):
            raise urllib.error.HTTPError(req.full_url, 422, "invalid symbol", {}, io.BytesIO(b"{}"))
        lo = datetime.fromisoformat(q["start"]).astimezone(ET).date()
        hi = datetime.fromisoformat(q["end"]).astimezone(ET).date()
        days = nyse.sessions(lo, hi)
        if q.get("page_token") != "2":
            days, token = days[:len(days) // 2 or 1], ("2" if len(days) > 1 else None)
        else:
            days, token = days[len(days) // 2:], None
        bars = {}
        for s in syms:
            rows = []
            for d in days:
                for hm in ("12:00", "15:45", "15:50", "15:59", "18:00"):  # 12:00 and 18:00 are outside the window
                    t = datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), ET).astimezone(UTC)
                    rows.append({"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "o": 10, "h": 11, "l": 9, "c": 10.5,
                                 "v": 100, "n": 3, "vw": 10.2})
            bars[s] = rows
        return _Resp(json.dumps({"bars": bars, "next_page_token": token}).encode())


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def http(monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return fake


def api():
    return ah.Alpaca("k", "s", budget=Budget("alpaca", BudgetSpec(), Clock()))


@pytest.fixture
def world(tmp_path):
    data = tmp_path / "data"
    days = mp.full_sessions(START, END)
    for k in ("AAA", "XLK", "SPY"):  # the full history of today's list (fetch_history.py)
        for m in sorted({mp.month_of(d) for d in days}):
            minute_file(data / "bars_1m" / f"symbol={k}" / f"{m}.parquet", [d for d in days if mp.month_of(d) == m])
    tg = mp.targets(store_keys(), membership(), CFG, START, END)
    return data, tg


# ---------------------------------------------------------------- targets


def test_every_key_of_the_pit_view_is_a_target_as_the_store_keys_it():
    tg = {t.key: t for t in mp.targets(store_keys(), membership(), CFG, START, END)}
    assert set(tg) == set(store_keys())
    assert tg["AAA"].seen_by_runs and tg["OLD@2018-02-21"].seen_by_runs
    # today (26 Nov) `keys` names C01@2026-11-19, the store holds C01@2026-11-05: still a target, flagged as unseen
    assert not tg[f"C01@{STORE_ASOF}"].seen_by_runs and tg[f"C01@{STORE_ASOF}"].asof == date(2026, 11, 5)
    built = {t.key: t for t in mp.targets(store_keys(), membership(date(2026, 11, 12)), CFG, START, END)}
    assert built[f"C01@{STORE_ASOF}"].seen_by_runs  # the day the store was built, key_map found it
    assert tg["XLK"].company is None and tg["SPY"].need == mp.full_sessions(START, END)


def test_a_former_member_needs_its_spell_and_a_tail_and_no_early_close():
    old = {t.key: t for t in mp.targets(store_keys(), membership(), CFG, START, END)}["OLD@2018-02-21"]
    assert old.member_days[-1] == date(2018, 2, 28) and date(2018, 3, 1) not in old.member_days
    tail = [d for d in old.need if d > date(2018, 2, 28)]
    assert len(tail) == mp.TAIL_SESSIONS and old.symbol == "OLD" and old.asof == date(2018, 2, 21)
    new = {t.key: t for t in mp.targets(store_keys(), membership(), CFG, START, END)}[f"NEW@{STORE_ASOF}"]
    assert new.need[0] == date(2018, 3, 1)
    assert nyse.early_close(date(2018, 7, 3)) and date(2018, 7, 3) not in new.need


def test_the_plan_skips_the_full_history_and_picks_the_cheaper_way_per_as_of_group(world):
    data, tg = world
    units = mp.plan(data, tg)
    keys = {k for u in units for k in u.days}
    assert "AAA" not in keys and "XLK" not in keys and "SPY" not in keys
    batch = [u for u in units if u.kind == "batch"]
    assert batch and all(u.asof == date(2026, 11, 5) for u in batch)  # 14 keys share the store's as-of date
    assert {k for u in batch for k in u.days} == {f"{c}@{STORE_ASOF}" for c in [*CURRENT]} | {
        f"NEW@{STORE_ASOF}", "GONE@2026-11-05"}
    assert [u.kind for u in units if "OLD@2018-02-21" in u.days] == ["range"] * 3  # Jan, Feb, the March tail


# ---------------------------------------------------------------- fetching


def test_fetch_asks_as_the_store_asked_keeps_the_window_and_flags_unknown_symbols(world, http):
    data, tg = world
    res = mp.fetch(api(), data, tg, now=datetime(2026, 11, 26, tzinfo=UTC), log=lambda s: None)
    assert res["units_left"] == 0 and not res["errors"]
    for q in http.requests:
        assert q["feed"] == "sip" and q["adjustment"] == "split" and q["timeframe"] == "1Min"
        assert datetime.fromisoformat(q["end"]).astimezone(ET).date() <= END
        assert "AAA" not in q["symbols"].split(",")
    batch = [q for q in http.requests if "C01" in q["symbols"]]
    assert all(q["asof"] == "2026-11-05" for q in batch)
    assert len(batch) == len(mp.full_sessions(START, END))  # one request a session for the whole group
    assert all(datetime.fromisoformat(q["start"]).astimezone(ET).strftime("%H:%M") == "15:45" for q in batch)
    old = [q for q in http.requests if q["symbols"] == "OLD"]
    assert old and all(q["asof"] == "2018-02-21" for q in old)
    for m in ("2018-01", "2018-02", "2018-03"):
        e = data / "bars_1550" / "symbol=OLD@2018-02-21" / f"{m}.empty"
        assert json.loads(e.read_text())["reason"] == "unknown_symbol"
    t = pq.read_table(data / "bars_1550" / "symbol=C01@2026-11-05" / "2018-03.parquet").to_pandas()
    et = t["ts"].dt.tz_convert(ET)
    assert set(et.dt.strftime("%H:%M")) == {"15:45", "15:50", "15:59"}  # 12:00 and 18:00 dropped
    assert set(et.dt.date) == {d for d in mp.full_sessions(START, END) if d.month == 3}
    n = len(http.requests)
    again = mp.fetch(api(), data, tg, log=lambda s: None)
    assert len(http.requests) == n and again["requests"] == 0  # resumable: nothing is asked twice


def test_a_range_unit_follows_the_pages_and_drops_early_closes_and_after_hours(world, http, monkeypatch):
    data, tg = world
    monkeypatch.setattr(mp, "PAGES_PER_MONTH", 0)  # every group goes key-month by key-month
    mp.fetch(api(), data, [t for t in tg if t.key == f"NEW@{STORE_ASOF}"], log=lambda s: None)
    reqs = [q for q in http.requests if q["symbols"] == "NEW"]
    assert any(q.get("page_token") == "2" for q in reqs)
    t = pq.read_table(data / "bars_1550" / f"symbol=NEW@{STORE_ASOF}" / "2018-07.parquet").to_pandas()
    days = set(t["ts"].dt.tz_convert(ET).dt.date)
    assert date(2018, 7, 3) not in days and date(2018, 7, 2) in days
    assert len(t) == 3 * len(days)


def test_failures_write_nothing_and_five_in_a_row_stop_the_run(world):
    data, tg = world

    class Down:
        calls = 0

        def get(self, path, params):
            Down.calls += 1
            raise HttpError(503, path, "down")

    res = mp.fetch(Down(), data, tg, log=lambda s: None)
    assert Down.calls == mp.MAX_ERRORS_IN_A_ROW and "failures in a row" in res["stopped"]
    assert not (data / "bars_1550").exists()


def test_the_time_budget_stops_between_units(world, http):
    data, tg = world
    ticks = iter([0.0, 0.0, 0.0, 999.0] + [999.0] * 1000)
    res = mp.fetch(api(), data, tg, max_minutes=1, log=lambda s: None, clock=lambda: next(ticks))
    assert res["units_done"] == 2 and "time budget" in res["stopped"] and res["units_left"] > 0


# ---------------------------------------------------------------- coverage and the reader


def test_coverage_counts_per_year_and_flags_keys_without_minutes(world, http):
    data, tg = world
    mp.fetch(api(), data, tg, log=lambda s: None)
    cov = mp.coverage(data, tg, START, END)
    (y,) = cov["years"]
    assert y["year"] == 2018 and y["members"] == 1 + 1 + 1 + 1 + len(CURRENT)  # AAA OLD GONE NEW C01-C12
    assert y["members_with_1550"] == y["members"] - 1  # OLD: Alpaca does not know it
    assert [f["key"] for f in cov["flagged_no_minutes"]] == ["OLD@2018-02-21"]
    assert cov["flagged_no_minutes"][0]["unknown_symbol_months"] == 2  # its member months
    assert cov["keys"] == len(tg) and cov["keys_seen_by_runs"] == 4  # AAA, OLD, XLK, SPY
    text = mp.render(cov)
    assert "| 2018 |" in text and "OLD@2018-02-21 (Alpaca: unknown)" in text
    assert json.loads((data / "bars_1550" / "coverage_cache.json").read_text())


def test_the_1550_reader_uses_the_new_store_where_the_full_history_has_no_month(tmp_path):
    d1, d2 = date(2018, 3, 1), date(2018, 4, 2)
    minute_file(tmp_path / "bars_1m" / "symbol=K@2018-05-01" / "2018-03.parquet", [d1], range(570, 960), px=20.0)
    minute_file(tmp_path / "bars_1550" / "symbol=K@2018-05-01" / "2018-03.parquet", [d1], px=99.0)  # ignored
    minute_file(tmp_path / "bars_1550" / "symbol=K@2018-05-01" / "2018-04.parquet", [d2], px=30.0)
    minute_file(tmp_path / "bars_1550" / "symbol=K@2018-05-01" / "2018-05.parquet", [date(2018, 5, 1)], px=40.0)
    out = m1550.session_1550(tmp_path, "K@2018-05-01", end="2018-04-30")
    assert list(out["c1555"]) == [20.0, 30.0] and list(out["c"]) == [20.0, 30.0]  # no month after `end`


# ---------------------------------------------------------------- the script and the runner


def test_the_script_refuses_the_holdout_and_too_fast_a_rate(tmp_path):
    with pytest.raises(SystemExit, match="hold-out"):
        script.main(["--data", str(tmp_path), "--end", "2025-01-02", "--status"])
    with pytest.raises(SystemExit, match="per-min"):
        script.main(["--data", str(tmp_path), "--per-min", "150", "--status"])
    with pytest.raises(SystemExit, match="daily store"):
        script.main(["--data", str(tmp_path), "--status"])


def test_the_runner_may_run_it_heavy_with_strict_flags_and_the_queued_jobs_are_valid():
    cfg = rr.load_allowed()
    spec = cfg.specs["scripts/fetch_minute_pit.py"]
    assert spec.env and spec.heavy and not spec.sec and spec.timeout_min >= 230
    mine = [rr.check_job(j, cfg) for j in rr.load_queue() if j["script"] == "scripts/fetch_minute_pit.py"]
    assert len(mine) >= 3 and all(j.issue == 794 for j in mine)
    bad = {"id": "x-794", "script": "scripts/fetch_minute_pit.py", "args": ["--per-min", "150"], "posts_to_issue": 794}
    with pytest.raises(rr.JobError):
        rr.check_job(bad, cfg)
    with pytest.raises(rr.JobError):
        rr.check_job({**bad, "args": ["--end", "2025-06-30"]}, cfg)
