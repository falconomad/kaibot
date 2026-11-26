"""Alpha Vantage EARNINGS backfill (decision 20, #797): parsing, point-in-time rule, budget, rate-limit stop, key."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from kaibot.data import av_earnings as ave
from kaibot.ingest import alphavantage as av
from kaibot.ingest import nyse_sessions as nyse

ROOT = Path(__file__).resolve().parents[2]


def payload(*rows):
    return {"symbol": "X", "annualEarnings": [], "quarterlyEarnings": list(rows)}


Q_POST = {"fiscalDateEnding": "2024-03-31", "reportedDate": "2024-05-02", "reportedEPS": "1.53",
          "estimatedEPS": "1.5", "surprise": "0.03", "surprisePercentage": "2", "reportTime": "post-market"}
Q_PRE = {"fiscalDateEnding": "2023-12-31", "reportedDate": "2024-02-01", "reportedEPS": "None",
         "estimatedEPS": "None", "surprise": "None", "surprisePercentage": "None", "reportTime": "pre-market"}


# ---------------------------------------------------------------- loader


def test_parse_reads_numbers_and_none():
    rows = ave.parse("X", payload(Q_POST, Q_PRE))
    assert [r.fiscal_date_ending for r in rows] == [date(2023, 12, 31), date(2024, 3, 31)]  # oldest first
    pre, post = rows
    assert pre.reported_eps is None and pre.estimated_eps is None and pre.surprise is None
    assert pre.surprise_pct is None and pre.report_time == "pre-market"
    assert post.reported_eps == 1.53 and post.estimated_eps == 1.5 and post.surprise == 0.03
    assert post.surprise_pct == 2.0 and post.report_time == "post-market"


@pytest.mark.parametrize("v", ["None", "", "-", "nan", "inf", "abc", None])
def test_missing_values_are_none_never_zero(v):
    (r,) = ave.parse("X", payload({**Q_POST, "surprisePercentage": v}))
    assert r.surprise_pct is None


def test_rows_without_dates_are_dropped_and_unknown_time_is_blank():
    rows = ave.parse("X", payload({**Q_POST, "reportedDate": "None"}, {**Q_POST, "fiscalDateEnding": ""},
                                  {**Q_POST, "reportTime": "during"}))
    assert len(rows) == 1 and rows[0].report_time == ""
    assert ave.parse("X", {}) == [] and ave.parse("X", {"Information": "x"}) == []


def test_conservative_rule_is_next_session_open_for_both_report_times():
    # Thu 2 May 2024 post-market -> Fri 3 May open; Thu 1 Feb 2024 pre-market -> Fri 2 Feb open (not 1 Feb)
    post, pre = ave.parse("X", payload(Q_POST))[0], ave.parse("X", payload(Q_PRE))[0]
    assert post.usable_from() == nyse.open_utc(date(2024, 5, 3))
    assert pre.usable_from() == nyse.open_utc(date(2024, 2, 2))
    fri = ave.parse("X", payload({**Q_POST, "reportedDate": "2024-05-03"}))[0]
    assert fri.next_session_after_report() == date(2024, 5, 6)  # over the weekend
    hol = ave.parse("X", payload({**Q_POST, "reportedDate": "2024-07-03"}))[0]
    assert hol.next_session_after_report() == date(2024, 7, 5)  # 4 July closed


def test_nominal_rule_pre_market_from_that_close_post_market_next_open():
    post, pre = ave.parse("X", payload(Q_POST))[0], ave.parse("X", payload(Q_PRE))[0]
    assert pre.usable_from("nominal") == nyse.close_utc(date(2024, 2, 1))
    assert post.usable_from("nominal") == nyse.open_utc(date(2024, 5, 3))
    for r in (pre, post):  # the conservative rule is never earlier than the nominal one
        assert r.usable_from() >= r.usable_from("nominal")
    with pytest.raises(ValueError):
        pre.usable_from("eager")


def test_known_as_of_never_sees_a_report_before_it_is_usable(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "X.json").write_text(json.dumps(payload(Q_POST, Q_PRE)))
    s = ave.AVEarnings(tmp_path)
    assert s.symbols() == ["X"]
    assert s.known_as_of("X", date(2024, 2, 1)) == []                  # the report day's open: not yet
    assert len(s.known_as_of("X", date(2024, 2, 2))) == 1               # next session's open
    assert len(s.known_as_of("X", nyse.close_utc(date(2024, 2, 1)), rule="nominal")) == 1
    assert len(s.known_as_of("X", nyse.close_utc(date(2024, 2, 1)) - timedelta(seconds=1), rule="nominal")) == 0
    assert len(s.known_as_of("X", date(2024, 5, 2))) == 1               # post-market on 2 May: not on 2 May
    assert s.latest_as_of("X", date(2024, 5, 3)).fiscal_date_ending == date(2024, 3, 31)
    assert s.known_as_of("NOPE", date(2030, 1, 1)) == []
    with pytest.raises(ValueError):
        s.known_as_of("X", datetime(2024, 5, 3, 12))  # naive datetime


def test_point_in_time_invariance_dropping_later_rows():
    qs = [{**Q_POST, "fiscalDateEnding": f"20{y}-0{m}-28", "reportedDate": f"20{y}-0{m + 1}-1{m}", "reportTime": t}
          for y in (21, 22, 23) for m in (1, 4, 7) for t in ("pre-market", "post-market")]
    rows = ave.parse("X", payload(*qs))
    for d in nyse.sessions(date(2021, 1, 1), date(2024, 1, 1))[::7]:
        k = ave.known(rows, d)
        assert all(r.reported_date < d for r in k)
        assert ave.known([r for r in rows if r.reported_date < d], d) == k


# ---------------------------------------------------------------- fetcher


class FakeAV:
    def __init__(self, answers=None, default=None):
        self.answers = answers or {}
        self.default = default if default is not None else payload(Q_POST)
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        a = self.answers.get(params["symbol"], self.default)
        if isinstance(a, Exception):
            raise a
        return a


class Clock:
    def __init__(self, t=datetime(2026, 11, 27, 1, 0, tzinfo=UTC)):
        self.t, self.slept = t, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += timedelta(seconds=s)


SYMS = [f"S{i:03d}" for i in range(50)]


def run(tmp_path, get, clock, **kw):
    return av.backfill(SYMS, tmp_path, get, now=clock.now, sleep=clock.sleep, **kw)


def test_budget_is_a_daily_total_across_runs_and_calls_are_13s_apart(tmp_path):
    get, c = FakeAV(), Clock()
    res, st = run(tmp_path, get, c)
    assert len(get.calls) == 20 == res.used_this_run and res.stopped == "budget"
    assert [p["symbol"] for p in get.calls] == SYMS[:20]  # alphabetical, deterministic
    assert get.calls[0]["function"] == "EARNINGS" and "apikey" not in get.calls[0]
    assert c.slept == [13.0] * 19
    res2, st2 = run(tmp_path, get, c)  # same UTC day: nothing left
    assert res2.used_this_run == 0 and len(get.calls) == 20 and st2.requests_today == 20
    assert sorted(p.stem for p in (tmp_path / "raw").glob("*.json")) == SYMS[:20]
    c.t += timedelta(days=1)  # next UTC day: the next 20, cached ones skipped
    run(tmp_path, get, c, budget=5)
    assert [p["symbol"] for p in get.calls[20:]] == SYMS[20:25]


def test_budget_ceiling_and_gap_floor(tmp_path):
    with pytest.raises(ValueError):
        run(tmp_path, FakeAV(), Clock(), budget=24)
    c = Clock()
    run(tmp_path, FakeAV(), c, budget=3, gap_s=1.0)
    assert c.slept == [13.0, 13.0]


@pytest.mark.parametrize("answer", [{"Information": "Thank you for using Alpha Vantage! ... 25 requests per day"},
                                    {"Note": "Our standard API call frequency is 5 calls per minute"}])
def test_rate_limit_answer_stops_at_once_and_is_not_stored(tmp_path, answer):
    get, c = FakeAV({"S003": answer}), Clock()
    res, st = run(tmp_path, get, c)
    assert [p["symbol"] for p in get.calls] == SYMS[:4] and res.stopped == "rate_limit"
    assert not (tmp_path / "raw" / "S003.json").exists() and "S003" not in st.fetched and "S003" not in st.missing
    assert st.requests_today == 4 and st.stopped_today == "rate_limit" and res.exit_code == 0
    res2, _ = run(tmp_path, get, c)  # later the same day: no request at all
    assert len(get.calls) == 4 and res2.stopped == "rate_limit"
    c.t += timedelta(days=1)
    run(tmp_path, get, c, budget=1)
    assert get.calls[-1]["symbol"] == "S003"  # retried the next day


def test_no_data_and_bad_key_and_errors(tmp_path):
    get = FakeAV({"S001": {}, "S002": {"Error Message": "Invalid API call."}})
    res, st = run(tmp_path, get, Clock(), budget=4)
    assert res.no_data == ["S001", "S002"] and set(st.missing) == {"S001", "S002"}
    bad = FakeAV(default={"Error Message": "the parameter apikey is invalid or missing."})
    res, st = run(tmp_path / "b", bad, Clock())
    assert res.stopped == "key" and res.exit_code == 2 and len(bad.calls) == 1
    err = FakeAV({"S000": RuntimeError("HTTP 0 for https://www.alphavantage.co/query?apikey=***")})
    res, st = run(tmp_path / "c", err, Clock())
    assert res.exit_code == 1 and res.stopped.startswith("error") and st.requests_today == 1


def test_refresh_only_after_everything_is_cached_oldest_first(tmp_path):
    c = Clock()
    syms = ["A", "B", "C"]
    st = av.State(fetched={"A": "2026-01-01T00:00:00+00:00", "B": "2026-11-01T00:00:00+00:00"})
    assert av.plan(syms, st, c.now()) == ["C"]  # uncached first, no refresh yet
    st.missing["C"] = "2025-12-01T00:00:00+00:00"
    assert av.plan(syms, st, c.now()) == ["C", "A"]  # stale (>100 days), oldest first; B is fresh


def test_av_symbol_and_state_roll():
    assert av.av_symbol("BRK.B") == "BRK-B"
    st = av.State(day="2026-11-26", requests_today=20, stopped_today="rate_limit")
    st.roll("2026-11-26")
    assert st.requests_today == 20
    st.roll("2026-11-27")
    assert (st.requests_today, st.stopped_today) == (0, "")


# ---------------------------------------------------------------- key and the script


def test_api_key_reads_only_its_name_data_env_first(tmp_path, monkeypatch):
    monkeypatch.delenv(av.KEY_NAME, raising=False)
    d, e = tmp_path / "data.env", tmp_path / ".env"
    e.write_text("ALPACA_API_KEY=trade\nALPHAVANTAGE_API_KEY=fromenv\n")
    assert av.api_key(str(e), str(tmp_path / "none.env")) == "fromenv"
    d.write_text("ALPACA_DATA_KEY=k\nALPHAVANTAGE_API_KEY=fromdata # comment\n")
    assert av.api_key(str(e), str(d)) == "fromdata"
    assert av.api_key(None, str(tmp_path / "none.env")) == ""
    monkeypatch.setenv(av.KEY_NAME, "fromos")
    assert av.api_key(None, str(tmp_path / "none.env")) == "fromos"


def _script():
    spec = importlib.util.spec_from_file_location("fetch_av_earnings", ROOT / "scripts" / "fetch_av_earnings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_missing_key_warns_and_exits_0_without_a_request(tmp_path, monkeypatch, capsys):
    mod = _script()
    monkeypatch.delenv(av.KEY_NAME, raising=False)
    monkeypatch.setenv("KAIBOT_DATA_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setattr(mod.av, "http_getter", lambda key: pytest.fail("no request without a key"))
    assert mod.main(["--data", str(tmp_path), "--env", str(tmp_path / "none2.env")]) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and av.KEY_NAME in out
    assert mod.main(["--data", str(tmp_path), "--check-key"]) == 1
    assert "NOT configured" in capsys.readouterr().out


def test_script_never_prints_the_key_and_refuses_a_big_budget(tmp_path, monkeypatch, capsys):
    mod = _script()
    secret = "SECRETKEY123"
    d = tmp_path / "data.env"
    d.write_text(f"ALPHAVANTAGE_API_KEY={secret}\n")
    monkeypatch.setenv("KAIBOT_DATA_ENV_FILE", str(d))
    monkeypatch.setattr(mod, "universe", lambda: ["AAA", "BBB"])
    monkeypatch.setattr(mod.av, "http_getter", lambda key: FakeAV())
    monkeypatch.setattr(mod.av.time, "sleep", lambda s: None)
    assert mod.main(["--data", str(tmp_path / "c"), "--budget", "3", "--no-calendar"]) == 0  # 1 stays reserved
    assert mod.main(["--data", str(tmp_path / "c"), "--check-key"]) == 0
    assert mod.main(["--data", str(tmp_path / "c"), "--status"]) == 0
    out = capsys.readouterr().out
    assert secret not in out and "2/2 symbols cached" in out and "configured (not shown)" in out
    state = (tmp_path / "c" / "state.json").read_text()
    assert secret not in state
    with pytest.raises(SystemExit):
        mod.main(["--data", str(tmp_path / "c"), "--budget", "24"])


def test_http_getter_sends_one_attempt_and_redacts(monkeypatch):
    from kaibot.common import http

    seen = {}

    def fake_get(url, **kw):
        seen.update(kw, url=url)
        return http.Response(200, {}, json.dumps(payload(Q_POST)).encode())

    monkeypatch.setattr(http, "get", fake_get)
    out = av.http_getter("K")({"function": "EARNINGS", "symbol": "X"})
    assert out["quarterlyEarnings"] and seen["attempts"] == 1 and seen["source"] == "alphavantage"
    assert seen["params"]["apikey"] == "K"
    red = http._redact("https://www.alphavantage.co/query?function=EARNINGS&apikey=SECRETK")
    assert "SECRETK" not in red and "apikey=" in red


def test_universe_is_the_500_current_members():
    syms = _script().universe()
    assert len(syms) == 500 and syms == sorted(syms) and "AAPL" in syms
