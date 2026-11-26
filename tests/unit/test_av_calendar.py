"""The upcoming-earnings calendar (display only, migration 0054): CSV parsing, the one reserved request of the
day's budget, the rate-limit/key fallbacks, and the push (upsert + prune in one call)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kaibot.ingest import alphavantage as av

ROOT = Path(__file__).resolve().parents[2]
CSV = ("symbol,name,reportDate,fiscalDateEnding,estimate,currency\r\n"
       "AAPL,Apple Inc,2027-01-28,2026-12-31,2.35,USD\r\n"
       "BRK-B,Berkshire Hathaway Inc,2027-02-22,2026-12-31,,USD\r\n"
       "ZZZZ,Not In The Universe,2027-01-05,2026-11-30,0.1,USD\r\n"
       "MSFT,Microsoft,2027-01-27,2026-12-31,None,USD\r\n"
       "MSFT,Microsoft,2027-01-29,2026-12-31,3.1,USD\r\n"
       "AMZN,Amazon,not-a-date,2026-12-31,1,USD\r\n")
UNI = ["AAPL", "AMZN", "BRK.B", "MSFT"]


def test_parse_keeps_the_universe_maps_share_classes_and_blank_estimates():
    rows = av.parse_calendar_csv(CSV, UNI)
    assert [(r["symbol"], r["report_date"]) for r in rows] == [
        ("MSFT", "2027-01-27"), ("AAPL", "2027-01-28"), ("BRK.B", "2027-02-22")]
    msft, aapl, brk = rows
    assert aapl["estimate"] == 2.35 and aapl["currency"] == "USD" and aapl["report_time"] is None
    assert brk["estimate"] is None and msft["estimate"] is None  # "", "None": omitted, never 0
    assert av.parse_calendar_csv("", UNI) == []


def test_parse_reads_a_time_of_day_column_when_there_is_one():
    text = ("﻿symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
            "AAPL,Apple,2027-01-28,2026-12-31,2.35,USD,post-market\nMSFT,MS,2027-01-27,2026-12-31,3,USD,bmo\n"
            "AMZN,Amazon,2027-01-30,2026-12-31,1,USD,\n")
    times = {r["symbol"]: r["report_time"] for r in av.parse_calendar_csv(text, UNI)}
    assert times == {"AAPL": "post-market", "MSFT": "pre-market", "AMZN": None}


class Get:
    def __init__(self, calendar=CSV, earnings=None):
        self.calendar, self.calls = calendar, []
        self.earnings = earnings or {"quarterlyEarnings": [{"fiscalDateEnding": "2024-03-31",
                                                            "reportedDate": "2024-05-02"}]}

    def __call__(self, params):
        self.calls.append(params)
        if params["function"] == "EARNINGS_CALENDAR":
            if isinstance(self.calendar, Exception):
                raise self.calendar
            return self.calendar
        return self.earnings


class Clock:
    def __init__(self):
        self.t, self.slept = datetime(2026, 11, 27, 1, 0, tzinfo=UTC), []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += timedelta(seconds=s)


SYMS = [f"S{i:03d}" for i in range(40)]


def test_calendar_takes_one_request_first_and_the_backfill_gets_19(tmp_path):
    get, c = Get(), Clock()
    res, st = av.fetch_calendar(UNI, tmp_path, get, now=c.now, sleep=c.sleep)
    assert res.status == "fetched" and len(res.rows) == 3 and st.requests_today == 1
    assert get.calls == [{"function": "EARNINGS_CALENDAR", "horizon": "3month"}]
    assert (tmp_path / "calendar" / "2026-11-27.csv").read_text().splitlines() == CSV.splitlines()
    bf, st = av.backfill(SYMS, tmp_path, get, now=c.now, sleep=c.sleep, reserve_calendar=True)
    assert bf.used_this_run == 19 and st.requests_today == 20
    assert c.slept[0] == 13.0  # the gap holds between the calendar and the first EARNINGS call too
    res2, st2 = av.fetch_calendar(UNI, tmp_path, get, now=c.now, sleep=c.sleep)  # later that day: from disk
    assert res2.status == "cached" and len(res2.rows) == 3 and len(get.calls) == 20 and st2.requests_today == 20


def test_without_the_calendar_today_the_backfill_still_leaves_one_request(tmp_path):
    get, c = Get(), Clock()
    bf, st = av.backfill(SYMS, tmp_path, get, now=c.now, sleep=c.sleep, reserve_calendar=True)
    assert bf.used_this_run == 19 and st.requests_today == 19
    res, st = av.fetch_calendar(UNI, tmp_path, get, now=c.now, sleep=c.sleep)
    assert res.status == "fetched" and st.requests_today == 20


@pytest.mark.parametrize("answer, status, code", [({"Information": "25 requests per day"}, "rate_limit", 0),
                                                  ({"Note": "5 calls per minute"}, "rate_limit", 0),
                                                  ({"Error Message": "apikey is invalid"}, "key", 2),
                                                  ("<html>oops</html>", "error: not the calendar CSV", 1)])
def test_calendar_fallbacks(tmp_path, answer, status, code):
    get, c = Get(calendar=answer), Clock()
    res, st = av.fetch_calendar(UNI, tmp_path, get, now=c.now, sleep=c.sleep)
    assert (res.status, res.exit_code, res.rows) == (status, code, []) and st.requests_today == 1
    assert not (tmp_path / "calendar" / "2026-11-27.csv").exists()
    bf, _ = av.backfill(SYMS, tmp_path, get, now=c.now, sleep=c.sleep, reserve_calendar=True)
    if status in ("rate_limit", "key"):
        assert bf.used_this_run == 0  # the day is stopped: nothing more is sent


def test_push_is_one_rpc_with_the_rows_and_the_time():
    calls = []

    class Store:
        def rpc(self, fn, args):
            calls.append((fn, args))
            return {"upserted": 1, "pruned": 0, "rows": 1}

    rows = av.parse_calendar_csv(CSV, ["AAPL"])
    assert av.push_calendar(rows, datetime(2026, 11, 27, 1, tzinfo=UTC), Store())["upserted"] == 1
    assert calls == [("earnings_calendar_refresh", {"p_rows": rows, "p_fetched_at": "2026-11-27T01:00:00+00:00"})]


def _script():
    spec = importlib.util.spec_from_file_location("fetch_av_earnings_cal", ROOT / "scripts" / "fetch_av_earnings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_calendar_without_service_key_or_migration_is_non_blocking(tmp_path, monkeypatch, capsys):
    mod = _script()
    secret = "SERVICEKEY-XYZ"
    env = tmp_path / ".env"
    env.write_text(f"SUPABASE_SERVICE_KEY={secret}\nALPACA_API_KEY=trade\n")
    monkeypatch.setattr(mod.av.time, "sleep", lambda s: None)
    # no service key: cached on disk, exit 0
    assert mod.run_calendar(UNI, tmp_path / "a", Get(), 20, None) == 0
    assert "WARNING: no SUPABASE_SERVICE_KEY" in capsys.readouterr().out

    class Missing:
        def rpc(self, fn, args):
            raise RuntimeError("POST rpc/earnings_calendar_refresh -> 404: PGRST202")

    assert mod.run_calendar(UNI, tmp_path / "b", Get(), 20, str(env), store_factory=lambda e: Missing()) == 0
    out = capsys.readouterr().out
    assert "migration 0054 not applied" in out and secret not in out

    class Ok:
        def __init__(self):
            self.n = 0

        def rpc(self, fn, args):
            self.n += 1
            return {"upserted": len(args["p_rows"]), "pruned": 0, "rows": 3}

    ok = Ok()
    get = Get()
    assert mod.run_calendar(UNI, tmp_path / "b", get, 20, str(env), store_factory=lambda e: ok) == 0  # from disk
    assert ok.n == 1 and get.calls == []  # the failed write is retried without a new request
    assert mod.run_calendar(UNI, tmp_path / "b", get, 20, str(env), store_factory=lambda e: ok) == 0
    assert ok.n == 1  # pushed today already: nothing to do
    assert secret not in capsys.readouterr().out


def test_make_store_reads_only_the_supabase_names(tmp_path):
    mod = _script()
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=trade\nSUPABASE_SERVICE_KEY=svc\n")
    s = mod.make_store(str(env))
    assert s is not None and s.h["apikey"] == "svc" and s.attempts == 1
    env.write_text("ALPACA_API_KEY=trade\n")
    assert mod.make_store(str(env)) is None and mod.make_store(None) is None
