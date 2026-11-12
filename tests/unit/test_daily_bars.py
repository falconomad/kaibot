"""Daily bar store, download side (story S84, #124): scripts/fetch_daily.py and kaibot.ingest.daily_bars.
Offline: urllib's urlopen is replaced by `FakeAlpaca`; the committed fixtures are synthetic and in Alpaca's shape."""

from __future__ import annotations

import csv
import json
import subprocess
from datetime import UTC, date, datetime, timedelta

import pyarrow.parquet as pq
import pytest
from daily_helpers import (
    FIXTURES,
    NOW,
    ROOT,
    START,
    THROUGH,
    FakeAlpaca,
    FakeClock,
    adjusted,
    api,
    bars,
    load_script,
    run,
    sessions,
    stamp,
)

from kaibot.common import ratelimit
from kaibot.common.ratelimit import BudgetSpec
from kaibot.ingest import alpaca_history as ah
from kaibot.ingest import daily_bars as db

DAYS = sessions(START)  # 2026-09-01 .. 2026-11-11
FIX_ACTIONS = json.loads((FIXTURES / "alpaca_corporate_actions.json").read_text())
FIX_RAW = json.loads((FIXTURES / "alpaca_bars_1day_raw.json").read_text())
FIX_ALL = json.loads((FIXTURES / "alpaca_bars_1day_all.json").read_text())


@pytest.fixture
def alpaca(monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    for sym, px in (("SPY", 600.0), ("AAA", 50.0), ("BBB", 20.0)):
        fake.add(sym, bars(DAYS, [px + i * 0.1 for i in range(len(DAYS))]))
    return fake


def store(tmp_path):
    return tmp_path / "data" / "daily"


def read_bars(tmp_path, key):
    return pq.read_table(db.bars_path(store(tmp_path), key))


# ---------------------------------------------------------------- what is asked of Alpaca


def test_bars_are_requested_raw_daily_sip_and_at_least_15_minutes_old(tmp_path, alpaca):
    assert run(tmp_path, "--symbols", "aaa") == 0
    asked = alpaca.bar_requests()
    assert [(s, q["adjustment"]) for s, q in asked] == [("SPY", "raw"), ("SPY", "all"), ("AAA", "raw"), ("AAA", "all")]
    for path, q, _ in alpaca.requests:
        if not path.endswith("/bars"):
            continue
        assert path.startswith("/v2/stocks/") and q["timeframe"] == "1Day" and q["feed"] == "sip"
        assert q["limit"] == "10000" and "asof" not in q, "today's universe is asked without an as-of date"
        assert datetime.fromisoformat(q["start"]) == datetime(2026, 9, 1, tzinfo=UTC)
        assert datetime.fromisoformat(q["end"]) <= NOW - timedelta(minutes=15), "SIP only when >= 15 min old"


def test_bars_are_stored_as_traded_one_row_per_complete_session(tmp_path, alpaca):
    today = date(2026, 11, 12)
    alpaca.add("AAA", bars([*DAYS, today], [50.0 + i for i in range(len(DAYS) + 1)]))
    during = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)  # 15:00 ET on the 12th: that session is still running
    assert run(tmp_path, "--symbols", "AAA", now=during) == 0
    t = read_bars(tmp_path, "AAA")
    assert t.schema.names == ["date", "o", "h", "l", "c", "v", "n", "vw", "available_ts"]
    rows = t.to_pylist()
    assert [r["date"] for r in rows] == DAYS, "the running session and its evening are left for the next run"
    assert rows[0]["c"] == 50.0 and rows[-1]["c"] == 50.0 + len(DAYS) - 1 and rows[0]["v"] == 1_000_000.0
    # a bar is complete, and readable by a point-in-time reader, from 20:15 New York time of its session
    assert rows[0]["available_ts"] == datetime(2026, 9, 2, 0, 15, tzinfo=UTC)  # 1 Sep, 20:15 EDT
    assert rows[-1]["available_ts"] == datetime(2026, 11, 12, 1, 15, tzinfo=UTC)  # 11 Nov, 20:15 EST
    meta = db.read_meta(db.bars_path(store(tmp_path), "AAA"))
    assert meta["adjustment"] == "raw" and meta["feed"] == "sip" and meta["through"] == "2026-11-11"
    assert (meta["first"], meta["last"], meta["rows"]) == ("2026-09-01", "2026-11-11", len(DAYS))


def test_the_evening_run_keeps_todays_session_only_after_extended_hours(tmp_path, alpaca):
    assert db.complete_through(datetime(2026, 11, 12, 1, 14, tzinfo=UTC)) == date(2026, 11, 10)  # 20:14 ET, 11 Nov
    assert db.complete_through(datetime(2026, 11, 12, 1, 15, tzinfo=UTC)) == date(2026, 11, 11)  # 20:15 ET
    assert db.complete_through(datetime(2026, 7, 7, 0, 15, tzinfo=UTC)) == date(2026, 7, 6)  # summer time


def test_a_long_history_is_read_page_by_page(tmp_path, alpaca):
    alpaca.page = 7
    assert run(tmp_path, "--symbols", "AAA") == 0
    assert read_bars(tmp_path, "AAA").num_rows == len(DAYS)
    tokens = [q.get("page_token") for s, q in alpaca.bar_requests() if s == "AAA" and q["adjustment"] == "raw"]
    assert tokens[:3] == [None, "7", "14"] and len(tokens) == -(-len(DAYS) // 7)


def test_the_committed_fixture_is_stored_unchanged(tmp_path, alpaca):
    alpaca.add("ZZZ", FIX_RAW["bars"], FIX_ALL["bars"])
    assert run(tmp_path, "--symbols", "ZZZ", start="2026-10-01") == 0
    rows = read_bars(tmp_path, "ZZZ").to_pylist()
    assert [(r["date"].isoformat(), r["c"], r["v"]) for r in rows][5:7] == [("2026-10-26", 410.0, 1e6),
                                                                              ("2026-10-27", 103.0, 4e6)]
    ref = pq.read_table(db.ref_path(store(tmp_path), "ZZZ")).to_pylist()
    assert ref[5]["c"] == 102.5 and ref[5]["v"] == 4e6, "Alpaca's adjusted series is kept apart, for the check"
    assert db.read_meta(db.ref_path(store(tmp_path), "ZZZ"))["adjustment"] == "all"


# ---------------------------------------------------------------- --symbols-file: tickers as of a date


def symbols_file(tmp_path, text):
    p = tmp_path / "former_members.csv"
    p.write_text(text)
    return str(p)


def test_symbols_file_lines_are_requested_as_of_their_date_and_stored_as_symbol_at_date(tmp_path, alpaca):
    old = sessions(START, 20)  # the old company with the ticker BBB stopped trading after 20 sessions
    alpaca.add("BBB", bars(old, [9.0] * 20), asof="2026-09-10")
    f = symbols_file(tmp_path, "symbol,asof_date\nBBB,2026-09-10\nspy,2026-10-01\n")
    assert run(tmp_path, "--symbols", "BBB", "--symbols-file", f) == 0
    asked = [(s, q.get("asof")) for s, q in alpaca.bar_requests() if q["adjustment"] == "raw"]
    assert asked == [("SPY", None), ("BBB", None), ("BBB", "2026-09-10"), ("SPY", "2026-10-01")]
    assert db.store_keys(store(tmp_path)) == ["BBB", "BBB@2026-09-10", "SPY", "SPY@2026-10-01"]
    assert read_bars(tmp_path, "BBB").num_rows == len(DAYS), "today's BBB"
    then = read_bars(tmp_path, "BBB@2026-09-10").to_pylist()
    assert len(then) == 20 and {r["c"] for r in then} == {9.0}, "the company that had the ticker on that date"
    meta = db.read_meta(db.bars_path(store(tmp_path), "BBB@2026-09-10"))
    assert meta["asof"] == "2026-09-10" and meta["symbol"] == "BBB"


def test_no_universe_downloads_only_the_file(tmp_path, alpaca):
    f = symbols_file(tmp_path, "symbol,asof_date\nAAA,2026-10-01\n")
    assert run(tmp_path, "--no-universe", "--symbols-file", f) == 0
    assert db.store_keys(store(tmp_path)) == ["AAA@2026-10-01"]


@pytest.mark.parametrize("text, why", [
    ("ticker,date\nAAA,2026-10-01\n", "header"),
    ("AAA,2026-10-01\n", "header"),
    ("symbol,asof_date\nAAA,01/10/2026\n", "line 2"),
    ("symbol,asof_date\nAAA\n", "two columns"),
    ("symbol,asof_date\n../etc,2026-10-01\n", "symbol"),
    ("symbol,asof_date\nAAA,2026-10-01\nBBB,2015-06-30\n", "line 3"),
    ("symbol,asof_date\nAAA,2027-01-04\n", "asof_date must be between"),
])
def test_a_bad_symbols_file_stops_before_any_request_or_file(tmp_path, alpaca, text, why):
    with pytest.raises(SystemExit, match=why):
        run(tmp_path, "--symbols-file", symbols_file(tmp_path, text))
    assert alpaca.requests == [] and not (tmp_path / "data").exists()


def test_symbols_file_contract_header_comments_and_repeats(tmp_path):
    f = symbols_file(tmp_path, "# former S&P 500 members (story S83)\nsymbol,asof_date\n\nTWTR,2022-06-30\n"
                               "twtr , 2022-06-30\nBRK.B,2020-01-02\n")
    assert db.FILE_HEADER == ["symbol", "asof_date"], "the contract with story S83 (#123)"
    assert [r.key for r in db.read_symbols_file(f)] == ["TWTR@2022-06-30", "BRK.B@2020-01-02"]
    assert db.parse_key("BRK.B@2020-01-02") == db.Request("BRK.B", date(2020, 1, 2))
    assert db.parse_key("SPY") == db.Request("SPY")


def test_an_as_of_request_answered_with_another_company_is_a_hole_not_data(tmp_path, alpaca):
    # Alpaca does not know the old company: it answers for the ticker alone, i.e. today's BBB, listed in October
    alpaca.add("BBB", bars(DAYS[30:], [20.0] * len(DAYS[30:])))
    f = symbols_file(tmp_path, "symbol,asof_date\nBBB,2026-09-10\n")
    assert run(tmp_path, "--no-universe", "--symbols-file", f) == 1, "a run that stored no bars at all says so"
    assert db.store_keys(store(tmp_path)) == [], "bars of another company are never stored under this key"
    hole = db.read_hole(db.bars_path(store(tmp_path), "BBB@2026-09-10").with_suffix(".empty"))
    assert hole["reason"] == "asof_not_covered" and hole["first_returned"] == DAYS[30].isoformat()


# ---------------------------------------------------------------- resumable


def test_a_second_run_asks_only_for_the_corporate_actions(tmp_path, alpaca):
    assert run(tmp_path, "--symbols", "AAA,BBB") == 0
    stamp_before = db.bars_path(store(tmp_path), "AAA").stat().st_mtime_ns
    n = len(alpaca.requests)
    assert run(tmp_path, "--symbols", "AAA,BBB") == 0
    again = alpaca.requests[n:]
    assert [p for p, _, _ in again] == ["/v1/corporate-actions"], "no bar is downloaded twice"
    assert db.bars_path(store(tmp_path), "AAA").stat().st_mtime_ns == stamp_before


def test_a_stopped_run_resumes_where_it_stopped(tmp_path, alpaca, capsys):
    alpaca.status["BBB"] = 401
    assert run(tmp_path, "--symbols", "AAA,BBB") == 1
    out = capsys.readouterr().out
    assert "failed at BBB: HTTP 401" in out and "run the same command again" in out
    assert db.store_keys(store(tmp_path)) == ["AAA", "SPY"] and alpaca.action_requests() == []
    del alpaca.status["BBB"]
    n = len(alpaca.requests)
    assert run(tmp_path, "--symbols", "AAA,BBB") == 0
    assert [s for s, _ in alpaca.bar_requests()[4 + 1:]] == ["BBB", "BBB"], "only the missing key is downloaded"
    assert len(alpaca.requests) - n == 3 and db.store_keys(store(tmp_path)) == ["AAA", "BBB", "SPY"]


def test_the_next_session_refreshes_live_series_but_not_ended_ones(tmp_path, alpaca):
    ended = sessions(START, 20)
    alpaca.add("OLD", bars(ended, [5.0] * 20), asof="2026-09-10")
    f = symbols_file(tmp_path, "symbol,asof_date\nOLD,2026-09-10\n")
    assert run(tmp_path, "--symbols", "AAA", "--symbols-file", f) == 0  # Wednesday 11 Nov, 22:00 New York
    assert len(alpaca.bar_requests()) == 6
    friday_night = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)  # Thursday's and Friday's sessions are new
    assert run(tmp_path, "--symbols", "AAA", "--symbols-file", f, now=friday_night) == 0
    asked = [s for s, _ in alpaca.bar_requests()[6:]]
    assert asked == ["SPY", "SPY", "AAA", "AAA"], "a delisted series is never downloaded again"
    n = len(alpaca.requests)
    saturday_night = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
    assert run(tmp_path, "--symbols", "AAA", "--symbols-file", f, now=saturday_night) == 0
    assert [p for p, _, _ in alpaca.requests[n:]] == ["/v1/corporate-actions"], "a weekend adds no session"


def test_refresh_downloads_everything_again(tmp_path, alpaca):
    assert run(tmp_path, "--symbols", "AAA") == 0
    n = len(alpaca.bar_requests())
    assert run(tmp_path, "--symbols", "AAA", "--refresh") == 0
    assert len(alpaca.bar_requests()) == 2 * n


def test_without_reference_one_request_per_key(tmp_path, alpaca):
    assert run(tmp_path, "--symbols", "AAA", "--no-reference") == 0
    assert [q["adjustment"] for _, q in alpaca.bar_requests()] == ["raw", "raw"]
    assert not (store(tmp_path) / "ref_all").exists()
    assert run(tmp_path, "--symbols", "AAA") == 0, "asking for the reference later downloads the key again"
    assert sorted(p.name for p in (store(tmp_path) / "ref_all").iterdir()) == ["symbol=AAA.parquet",
                                                                              "symbol=SPY.parquet"]


def test_stored_bars_are_never_replaced_by_a_hole(tmp_path, alpaca, capsys):
    assert run(tmp_path, "--symbols", "AAA,BBB") == 0
    before = read_bars(tmp_path, "AAA")
    alpaca.add("AAA", [])  # a hiccup: Alpaca answers without bars today
    alpaca.status["BBB"] = 404
    capsys.readouterr()
    assert run(tmp_path, "--symbols", "AAA,BBB", "--refresh") == 0
    out = capsys.readouterr().out
    assert "AAA: Alpaca returned nothing usable now (no_bars); the stored bars 2026-09-01..2026-11-11 are kept" in out
    assert "BBB: Alpaca returned nothing usable now (http_404)" in out and "2 kept as stored, 0 new holes" in out
    assert read_bars(tmp_path, "AAA").equals(before) and db.store_keys(store(tmp_path)) == ["AAA", "BBB", "SPY"]
    assert not list((store(tmp_path) / "bars").glob("*.empty"))


# ---------------------------------------------------------------- holes


def test_unknown_and_empty_tickers_are_logged_as_holes_and_not_asked_again(tmp_path, alpaca, capsys):
    alpaca.status["NOPE"] = 404
    alpaca.add("VOID", [])
    assert run(tmp_path, "--symbols", "NOPE,VOID,AAA") == 0, "holes are data, not a failure"
    out = capsys.readouterr().out
    assert "NOPE: no usable bars (http_404)" in out and "VOID: no usable bars (no_bars)" in out
    with db.holes_path(store(tmp_path)).open() as fh:
        holes = list(csv.DictReader(fh))
    assert [(h["key"], h["reason"], h["http"]) for h in holes] == [("NOPE", "http_404", "404"),
                                                                   ("VOID", "no_bars", "0")]
    assert db.store_keys(store(tmp_path)) == ["AAA", "SPY"]
    n = len(alpaca.bar_requests())
    assert run(tmp_path, "--symbols", "NOPE,VOID,AAA") == 0
    assert len(alpaca.bar_requests()) == n, "a known hole costs no request"
    alpaca.add("VOID", bars(DAYS, [3.0] * len(DAYS)))
    assert run(tmp_path, "--symbols", "NOPE,VOID,AAA", "--retry-holes") == 0
    assert "VOID" in db.store_keys(store(tmp_path))
    with db.holes_path(store(tmp_path)).open() as fh:
        assert [h["key"] for h in csv.DictReader(fh)] == ["NOPE"]


def test_an_answer_in_another_shape_stops_the_run_instead_of_storing_nonsense(tmp_path, alpaca, capsys):
    alpaca.add("AAA", [{"timestamp": "2026-10-01", "close": 1.0}])  # not Alpaca's bar fields
    assert run(tmp_path, "--symbols", "AAA") == 1
    out = capsys.readouterr().out
    assert "answer for AAA could not be read (KeyError" in out and "Tell the Data team" in out
    assert db.store_keys(store(tmp_path)) == ["SPY"]


def test_a_page_token_that_never_ends_is_an_error(monkeypatch):
    class Loop:
        def get(self, path, params):
            return {"bars": [], "corporate_actions": {}, "next_page_token": "again"}

    monkeypatch.setattr(db, "MAX_PAGES", 5)
    with pytest.raises(ValueError, match="did not end after 5 pages"):
        db.fetch_daily(Loop(), "AAA", date(2026, 9, 1), NOW)
    with pytest.raises(ValueError, match="did not end after 5 pages"):
        db.fetch_actions(Loop(), ["AAA"], date(2026, 9, 1), THROUGH)


def test_refusals_before_anything_was_stored_stop_the_run_and_mark_no_holes(tmp_path, alpaca, capsys):
    for sym in ("SPY", "AAA", "BBB"):
        alpaca.status[sym] = 400  # Alpaca does not accept the request at all
    assert run(tmp_path, "--symbols", "AAA,BBB") == 1
    out = capsys.readouterr().out
    assert "refused the first 3 requests (http_400)" in out and "Tell the Data team" in out
    assert not list((store(tmp_path) / "bars").glob("*.empty")), "a wrong request must not turn tickers into holes"
    assert len(alpaca.requests) == 3 and alpaca.action_requests() == []
    alpaca.status.clear()
    assert run(tmp_path, "--symbols", "AAA,BBB") == 0 and db.store_keys(store(tmp_path)) == ["AAA", "BBB", "SPY"]


def test_retired_tickers_at_the_head_of_a_file_are_holes_not_a_reason_to_stop(tmp_path, alpaca):
    for sym in ("GA", "GB", "GC"):
        alpaca.status[sym] = 404
    f = symbols_file(tmp_path, "symbol,asof_date\nGA,2026-09-10\nGB,2026-09-10\nGC,2026-09-10\nAAA,2026-09-10\n")
    assert run(tmp_path, "--no-universe", "--symbols-file", f) == 0
    assert db.store_keys(store(tmp_path)) == ["AAA@2026-09-10"]
    assert len(list((store(tmp_path) / "bars").glob("*.empty"))) == 3


def test_many_refused_keys_after_good_ones_are_holes_with_a_warning(tmp_path, alpaca, capsys):
    gone = [f"G{chr(65 + i)}" for i in range(7)]
    for sym in gone:
        alpaca.status[sym] = 404
    assert run(tmp_path, "--symbols", "AAA," + ",".join(gone)) == 0
    out = capsys.readouterr().out
    assert "WARNING: Alpaca refused 7 of 9 keys as invalid" in out
    assert len(list((store(tmp_path) / "bars").glob("*.empty"))) == 7


# ---------------------------------------------------------------- keys, budget, input checks


def test_keys_are_never_printed(tmp_path, alpaca, capsys):
    alpaca.status["SPY"] = 403
    assert run(tmp_path, "--symbols", "AAA", client=api("key-abc123", "secret-xyz789")) == 1
    out = capsys.readouterr().out
    assert "HTTP 403" in out and "abc123" not in out and "xyz789" not in out
    assert len(alpaca.requests) == 1, "a refused key is not tried symbol after symbol"


def test_env_file_is_required_and_requests_share_the_alpaca_budget(tmp_path, alpaca, capsys, monkeypatch):
    fd = load_script()
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(tmp_path / "from-env-var.env"))
    assert fd.main(["--symbols", "AAA", "--data", str(tmp_path / "data")], now=NOW) == 2
    assert "from-env-var.env" in capsys.readouterr().out and alpaca.requests == []
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=abc123secret\n")
    assert fd.main(["--env", str(env), "--symbols", "AAA", "--data", str(tmp_path / "data")], now=NOW) == 2
    assert "abc123secret" not in capsys.readouterr().out and alpaca.requests == []

    reg = ratelimit.Registry(clock=FakeClock())
    monkeypatch.setattr(ah, "REGISTRY", reg)
    monkeypatch.setenv("SSL_CERT_FILE", "unchanged")
    env.write_text("ALPACA_API_KEY=abc\nALPACA_SECRET=def\n")
    assert fd.main(["--env", str(env), "--symbols", "AAA", "--data", str(tmp_path / "data"), "--start", START,
                    "--per-min", "40"], now=NOW) == 0
    shared = ratelimit.load_specs()["alpaca"].windows
    assert reg.get("alpaca").spec.windows == (*shared, (40, 60.0)), "config/sources.yaml budget plus --per-min"
    assert {h["Apca-api-key-id"] for _, _, h in alpaca.requests} == {"abc"}


def test_per_min_caps_the_request_rate(tmp_path, alpaca):
    clock = FakeClock()
    slow = api(spec=BudgetSpec(((150, 60.0), (2, 60.0))), clock=clock)
    assert run(tmp_path, "--symbols", "AAA", client=slow) == 0
    assert len(alpaca.requests) == 5 and clock.t - 1000.0 >= 120.0  # 5 requests at 2/min: two full waits


@pytest.mark.parametrize("args", [["--symbols", "AAA,../x"], ["--start", "2031-01-01"], ["--start", "soon"],
                                  ["--symbols-file", "missing.csv"], ["--no-universe"],
                                  ["--symbols", "AAA", "--no-universe"], ["--per-min", "0"]])
def test_bad_input_stops_before_any_request_or_file(tmp_path, alpaca, args):
    fd = load_script()
    with pytest.raises(SystemExit) as e:
        fd.main(["--env", str(tmp_path / "missing.env"), "--data", str(tmp_path / "data"), *args], now=NOW)
    assert e.value.code not in (0, None)
    assert alpaca.requests == [] and not (tmp_path / "data").exists()


def test_the_default_download_is_spy_the_sector_etfs_and_the_history_universe():
    fd = load_script()
    reqs = fd.build_requests(fd.argparse.Namespace(start="2016-01-01", no_universe=False, symbols="", symbols_file=None,
                                                   universe=str(ROOT / "config" / "universe_history.yaml")), THROUGH)
    keys = [r.key for r in reqs]
    assert len(keys) == len(set(keys)) == 300 and keys[0] == "SPY" and {"XLC", "XLK", "NVDA", "BRK.B"} - set(keys) == {
        "BRK.B"}
    assert all(r.asof is None for r in reqs)


# ---------------------------------------------------------------- corporate actions


def test_corporate_actions_request_follows_the_documentation(tmp_path, alpaca):
    assert run(tmp_path, "--symbols", "AAA,BBB") == 0
    (q,) = alpaca.action_requests()
    assert q["symbols"] == "AAA,BBB,SPY" and q["limit"] == "1000"
    assert q["types"] == "forward_split,reverse_split,stock_dividend,cash_dividend,spin_off,name_change"
    assert q["start"] == START and q["end"] == "2027-03-11", "announced actions of the next 120 days are included"


def test_corporate_actions_are_asked_in_chunks_and_read_page_by_page(alpaca):
    syms = [f"S{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(120)]
    for i, s in enumerate(syms):
        alpaca.act("cash_dividend", symbol=s, rate=0.1 + i / 1000, ex_date="2026-10-01", process_date="2026-10-15")
    alpaca.ca_page = 30
    rows, info = db.fetch_actions(api(), syms, date(2026, 9, 1), date(2026, 11, 11))
    assert len(rows) == 120 and info["requests"] == 5 and info["mode"] == "one_range"
    asked = alpaca.action_requests()
    assert [len(q["symbols"].split(",")) for q in asked] == [50, 50, 50, 50, 20]
    assert [q.get("page_token") for q in asked] == [None, "30", None, "30", None]


def test_a_refused_range_is_asked_again_year_by_year(alpaca):
    alpaca.act("cash_dividend", symbol="AAA", rate=0.5, ex_date="2024-03-01", process_date="2024-03-15")
    alpaca.act("cash_dividend", symbol="AAA", rate=0.6, ex_date="2026-10-01", process_date="2026-10-15")
    alpaca.act("cash_dividend", symbol="AAA", rate=0.7, ex_date="2026-11-05", process_date="2026-11-25")


    def one_year_at_most(q):
        return 400 if (date.fromisoformat(q["end"]) - date.fromisoformat(q["start"])).days > 366 else None

    alpaca.ca_status = one_year_at_most
    said = []
    rows, info = db.fetch_actions(api(), ["AAA", "BBB"], date(2024, 1, 1), date(2026, 11, 11), chunk=1,
                                  log=said.append)
    assert sorted(r["cash"] for r in rows) == [0.5, 0.6, 0.7], "the dividend that is announced but not paid is found"
    assert info["mode"] == "yearly_windows" and info["future"] == "included"
    spans = [(q["symbols"], q["start"], q["end"]) for q in alpaca.action_requests()]
    assert spans == [("AAA", "2024-01-01", "2027-03-11"), ("AAA", "2024-01-01", "2024-12-31"),
                     ("AAA", "2025-01-01", "2025-12-31"), ("AAA", "2026-01-01", "2026-11-11"),
                     ("AAA", "2026-11-12", "2027-03-11"), ("BBB", "2024-01-01", "2024-12-31"),
                     ("BBB", "2025-01-01", "2025-12-31"), ("BBB", "2026-01-01", "2026-11-11"),
                     ("BBB", "2026-11-12", "2027-03-11")], "the refused single range is tried once, not per chunk"
    assert info["requests"] == 9 and "one calendar year at a time" in said[0]


def test_a_refused_future_window_is_noted_and_the_past_is_still_fetched(alpaca):
    alpaca.act("cash_dividend", symbol="AAA", rate=0.6, ex_date="2026-10-01", process_date="2026-10-15")
    alpaca.ca_status = lambda q: 422 if q["end"] > "2026-11-11" else None
    rows, info = db.fetch_actions(api(), ["AAA"], date(2026, 1, 1), date(2026, 11, 11))
    assert [r["cash"] for r in rows] == [0.6] and info["future"] == "refused" and info["end"] == "2026-11-11"


class Verbatim:
    """Serves one response exactly as written (the committed fixture)."""

    def __init__(self, doc):
        self.doc, self.calls = doc, []

    def get(self, path, params):
        self.calls.append((path, params))
        return self.doc


def test_the_response_shape_of_the_fixture_is_parsed_field_by_field():
    rows, info = db.fetch_actions(Verbatim(FIX_ACTIONS), ["ZZZ"], date(2026, 9, 1), date(2026, 11, 11))
    assert info["requests"] == 1 and info["unusable_rows"] == 2, "a zero rate and a row without dates are left out"
    by = {r["id"] or r["symbol"]: r for r in rows if r["type"] != "name_change"}
    split = by["f-1"]
    assert (split["type"], split["symbol"], split["ex_date"], split["ratio"]) == ("forward_split", "ZZZ",
                                                                                 date(2026, 10, 27), 4.0)
    assert (split["record_date"], split["payable_date"], split["cusip"]) == (date(2026, 10, 23), date(2026, 10, 26),
                                                                           "000000001")
    assert by["NVDX"]["ratio"] == 10.0 and by["NVDX"]["payable_date"] is None, "optional fields may be missing"
    assert by["NVDQ"]["type"] == "reverse_split" and by["NVDQ"]["ratio"] == pytest.approx(1 / 3)
    assert by["NVDQ"]["cusip"] == "000000011"
    assert by["s-1"]["type"] == "stock_dividend" and by["s-1"]["ratio"] == pytest.approx(1.05)
    d1, d3, d4 = by["d-1"], by["d-3"], by["d-4"]
    assert (d1["cash"], d1["special"], d1["ex_date"], d1["process_date"]) == (1.2, False, date(2026, 10, 22),
                                                                            date(2026, 11, 5))
    assert d3["cash"] == 3.0 and d3["special"] is True
    assert (d4["special"], d4["foreign"]) == (False, True), "booleans sent as strings are read as booleans"
    spin = by["o-1"]
    assert (spin["type"], spin["symbol"], spin["ex_date"]) == ("spin_off", "ZZZ", date(2026, 10, 28))
    names = [r for r in rows if r["type"] == "name_change"]
    assert sorted(r["symbol"] for r in names) == ["ZZO", "ZZZ"] and names[0]["ex_date"] == date(2026, 10, 20)
    assert json.loads(d1["raw"])["id"] == "d-1", "the row is kept as Alpaca sent it"
    assert not any("cash_merger" in r["type"] for r in rows), "types the store does not read are ignored"


def test_an_unexpected_response_shape_is_an_error_not_an_empty_table():
    with pytest.raises(ValueError, match="unexpected corporate-actions response"):
        db.fetch_actions(Verbatim({"corporate_actions": [{"symbol": "ZZZ"}]}), ["ZZZ"], date(2026, 9, 1), THROUGH)


def test_actions_are_tied_to_keys_and_repeats_are_flagged_not_deleted(tmp_path, alpaca):
    alpaca.add("ZZZ", FIX_RAW["bars"], FIX_ALL["bars"])
    alpaca.actions = {k: v for k, v in FIX_ACTIONS["corporate_actions"].items() if k != "cash_mergers"}
    assert run(tmp_path, "--symbols", "ZZZ", start="2026-10-01") == 0
    t = pq.read_table(db.actions_path(store(tmp_path))).to_pylist()
    assert t and {r["key"] for r in t} == {"ZZZ"}
    got = [(r["type"], r["ex_date"].isoformat(), r["cash"] if r["type"] == "cash_dividend" else r["ratio"],
            r["special"], r["dup"]) for r in t if r["type"] in db.ADJUSTING_TYPES]
    assert got == [("cash_dividend", "2026-10-22", 1.2, False, False),  # CUSIP 000000001
                   ("cash_dividend", "2026-10-22", 1.2, False, True),  # the same payment under a second CUSIP
                   ("cash_dividend", "2026-10-22", 3.0, True, False),  # a special dividend that day is its own payment
                   ("forward_split", "2026-10-27", 4.0, False, False),
                   ("cash_dividend", "2026-10-29", 0.3, False, False)]
    info = {r["type"] for r in t} - set(db.ADJUSTING_TYPES)
    assert info == {"spin_off", "name_change"}, "kept as information"
    meta = db.read_meta(db.actions_path(store(tmp_path)))
    assert meta["duplicates"] == 1 and meta["by_type"]["cash_dividend"] == 4 and meta["first_ex_date"] == "2026-10-22"


def test_a_reused_ticker_gets_only_the_actions_inside_its_own_bars():
    old = db.Span("DOW@2017-06-30", "DOW", date(2016, 1, 4), date(2017, 8, 31), ended=True)
    new = db.Span("DOW", "DOW", date(2019, 3, 20), date(2026, 11, 11), ended=False)

    def div(ex, cash):
        return db.normalise_action("cash_dividend", {"symbol": "DOW", "rate": cash, "ex_date": ex})[0]

    rows = [div("2016-03-29", 0.46), div("2017-06-28", 0.46), div("2019-05-30", 0.70), div("2026-11-27", 0.35),
            div("2019-03-20", 9.99), div("2016-01-04", 9.99)]
    got = sorted((r["key"], r["ex_date"].isoformat()) for r in db.attribute_actions(rows, [old, new]))
    assert got == [("DOW", "2019-05-30"), ("DOW", "2026-11-27"),  # announced, not yet ex: kept for today's company
                   ("DOW@2017-06-30", "2016-03-29"), ("DOW@2017-06-30", "2017-06-28")]


def test_a_refused_corporate_actions_endpoint_keeps_the_bars_and_says_so(tmp_path, alpaca, capsys):
    alpaca.ca_status = lambda q: 403
    assert run(tmp_path, "--symbols", "AAA") == 1
    out = capsys.readouterr().out
    assert "Corporate actions failed: HTTP 403" in out and "bars are stored" in out and "plan" in out
    assert db.store_keys(store(tmp_path)) == ["AAA", "SPY"] and not db.actions_path(store(tmp_path)).exists()
    alpaca.ca_status = None
    assert run(tmp_path, "--symbols", "AAA") == 0 and db.actions_path(store(tmp_path)).exists()


def test_no_actions_flag_and_status(tmp_path, alpaca, capsys):
    assert run(tmp_path, "--symbols", "AAA", "--no-actions") == 0
    assert alpaca.action_requests() == [] and "not fetched yet" in capsys.readouterr().out
    alpaca.status["NOPE"] = 404
    alpaca.act("cash_dividend", symbol="AAA", rate=0.25, ex_date="2026-10-01", process_date="2026-10-15")
    assert run(tmp_path, "--symbols", "AAA,NOPE") == 0
    capsys.readouterr()
    n = len(alpaca.requests)
    assert run(tmp_path, "--symbols", "AAA,NOPE", "--status") == 0
    out = capsys.readouterr().out
    assert len(alpaca.requests) == n, "--status sends nothing and needs no key"
    assert "3/3 requested keys answered" in out and "2 keys with bars" in out and "1 holes" in out
    assert f"{2 * len(DAYS):,} sessions 2026-09-01..2026-11-11" in out and "cash_dividend 1" in out


# ---------------------------------------------------------------- the files a user starts


def test_wrapper_is_valid_shell_and_never_reads_the_secrets_aloud():
    sh = ROOT / "scripts" / "fetch_daily.sh"
    assert subprocess.run(["bash", "-n", str(sh)], capture_output=True).returncode == 0
    text = sh.read_text()
    assert "--symbols-file" in text and "data/universe/former_members.csv" in text
    for line in text.splitlines():
        if "ENV_FILE" in line and not line.lstrip().startswith("#"):
            assert not any(w in line for w in ("cat ", "source ", "set -x", "echo \"$(")), line
    assert "grep -q" in text and "grep -Eq" in text, "the wrapper only checks that the key names exist"


def test_sources_yaml_names_the_new_uses_under_the_alpaca_budget():
    specs = ratelimit.load_specs()
    assert specs["alpaca"].windows == ((150, 60.0),), "the shared budget is unchanged"
    assert specs["alpaca_daily_bars"].unlimited and specs["alpaca_corporate_actions"].unlimited, \
        "documented entries: their requests count against `alpaca`"


def test_timestamps_in_fixtures_are_midnight_new_york():
    assert stamp(date(2026, 10, 27)) == "2026-10-27T04:00:00Z" and stamp(date(2026, 11, 11)) == "2026-11-11T05:00:00Z"
    assert db.session_date("2026-10-27T04:00:00Z") == date(2026, 10, 27)
    assert db.session_date("2026-11-11T05:00:00Z") == date(2026, 11, 11)
    assert [db.session_date(b["t"]) for b in adjusted(FIX_RAW["bars"])][0] == date(2026, 10, 19)
