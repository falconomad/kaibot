"""Earnings announcement dates from SEC 8-K Item 2.02 (story S85, #125): sessions, parsing, CIK mapping, the
resumable fetch, the point-in-time reader, the forward-calendar snapshot and the coverage report. Offline: every
SEC / Finnhub response comes from tests/fixtures/earnings."""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.ingest import earnings_calendar as cal
from kaibot.ingest import earnings_pit as pit_e
from kaibot.ingest import earnings_sec as sec
from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest import sp500_pit as pit
from kaibot.replay.earnings import blackout, calendar

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "earnings"
ROOT = Path(__file__).resolve().parents[2]
D = date


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def et(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=nyse.ET)


# ---------------------------------------------------------------- NYSE sessions


@pytest.mark.parametrize("year,n", [(2016, 252), (2017, 251), (2018, 251), (2019, 252), (2020, 253), (2021, 252),
                                    (2022, 251), (2023, 250), (2024, 252)])
def test_session_counts_match_nyse(year, n):
    assert len(nyse.sessions(D(year, 1, 1), D(year, 12, 31))) == n


def test_holiday_rules():
    assert nyse.holidays(2024) == {D(2024, 1, 1), D(2024, 1, 15), D(2024, 2, 19), D(2024, 3, 29), D(2024, 5, 27),
                                   D(2024, 6, 19), D(2024, 7, 4), D(2024, 9, 2), D(2024, 11, 28), D(2024, 12, 25)}
    assert nyse.is_session(D(2021, 12, 31))  # New Year's Day 2022 (a Saturday) is not moved into December
    assert not nyse.is_session(D(2027, 6, 18))  # Juneteenth 2027 is a Saturday: observed on Friday
    assert nyse.is_session(D(2021, 6, 18))  # no Juneteenth before 2022
    assert not nyse.is_session(D(2018, 12, 5)) and not nyse.is_session(D(2025, 1, 9))
    assert nyse.early_close(D(2024, 11, 29)) and nyse.early_close(D(2024, 12, 24))
    assert not nyse.early_close(D(2024, 12, 23))


def test_first_session_opening_after():
    assert nyse.first_session_opening_after(et("2024-10-29T07:00:00")) == D(2024, 10, 29)  # before the open
    assert nyse.first_session_opening_after(et("2024-10-29T09:30:00")) == D(2024, 10, 30)  # at the open: next
    assert nyse.first_session_opening_after(et("2024-10-29T11:00:00")) == D(2024, 10, 30)  # intraday
    assert nyse.first_session_opening_after(et("2024-11-01T16:05:00")) == D(2024, 11, 4)  # Friday evening
    assert nyse.first_session_opening_after(et("2024-11-27T17:00:00")) == D(2024, 11, 29)  # over Thanksgiving
    # DST: 13:30 UTC is 09:30 EDT in summer (at the open) but 08:30 EST in winter (before it)
    assert nyse.first_session_opening_after(utc("2024-07-02T13:30:00")) == D(2024, 7, 3)
    assert nyse.first_session_opening_after(utc("2024-12-03T13:30:00")) == D(2024, 12, 3)
    with pytest.raises(ValueError):
        nyse.first_session_opening_after(datetime(2024, 1, 2, 12))


def test_last_session_closed_by():
    assert nyse.last_session_closed_by(et("2024-10-29T16:05:00")) == D(2024, 10, 29)
    assert nyse.last_session_closed_by(et("2024-10-29T11:00:00")) == D(2024, 10, 28)
    assert nyse.last_session_closed_by(et("2024-10-29T07:00:00")) == D(2024, 10, 28)
    assert nyse.last_session_closed_by(et("2024-11-04T07:00:00")) == D(2024, 11, 1)
    assert nyse.last_session_closed_by(et("2024-11-29T13:30:00")) == D(2024, 11, 29)  # early close at 13:00


# ---------------------------------------------------------------- parsing


def _doc(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_keeps_8k_item_202_since_2016_only():
    filings, periodic = sec.parse_block(_doc("CIK0000000101-submissions-001.json"))
    assert [f.accession for f in filings] == ["0000000101-16-000002", "0000000101-16-000001"]  # no 8-K/A, 5.07, 2015
    assert filings[1].accepted_ts == utc("2016-01-26T21:05:00")
    _, periodic = sec.parse_block(_doc("CIK0000000101.json")["filings"]["recent"])
    assert periodic == [D(2016, 11, 3)]


def test_parse_rejects_bad_schema():
    with pytest.raises(ValueError):
        sec.parse_block({"accessionNumber": ["x"]})
    with pytest.raises(ValueError):
        sec.parse_block({"accessionNumber": ["x"], "filingDate": [], "acceptanceDateTime": [], "form": [],
                         "items": []})


def test_older_files_only_reaching_2016():
    assert sec.older_files(_doc("CIK0000000101.json")) == ["CIK0000000101-submissions-001.json"]


def test_company_from_submissions_merges_files_and_skips_malformed():
    old = "CIK0000000101-submissions-001.json"
    co = sec.company_from_submissions("0000000101", _doc("CIK0000000101.json"), {old: _doc(old)})
    assert [f.accession[-2:] for f in co.filings] == ["01", "02", "06", "07", "08", "04"]
    assert co.requests == 2 and co.former_names == ["XYZ HOLDINGS INC"]
    with pytest.raises(ValueError):
        sec.company_from_submissions("0000000999", _doc("CIK0000000101.json"), {})
    with pytest.raises(ValueError):  # a needed older file is missing
        sec.company_from_submissions("0000000101", _doc("CIK0000000101.json"), {})
    again = sec.Company.from_json(json.loads(json.dumps(co.to_json())))
    assert again.filings == co.filings and again.periodic == co.periodic


class FakeSec:
    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        name = url.rsplit("/", 1)[-1]
        p = FIX / name
        if not p.is_file():
            raise RuntimeError(f"HTTP 404 for {url}")
        return p.read_bytes()


def test_cache_is_resumable_and_refresh_reads_only_recent(tmp_path):
    get = FakeSec()
    c = sec.Cache(tmp_path, get)
    co = c.company("0000000101")
    assert len(get.urls) == 2 and len(co.filings) == 6
    assert sec.Cache(tmp_path, get).company("0000000101").filings == co.filings and len(get.urls) == 2
    sec.Cache(tmp_path, get).company("0000000101", refresh=True)
    assert len(get.urls) == 3 and get.urls[-1].endswith("CIK0000000101.json")
    assert len(sec.Cache(tmp_path, None).load("0000000101").filings) == 6  # merged, not duplicated
    bad = sec.Cache(tmp_path, get)
    assert bad.company("0000000555") is None and "0000000555" in bad.errors
    assert not (tmp_path / "CIK0000000555.json").exists()


def test_sec_getter_requires_a_contact_and_declares_it(monkeypatch):
    with pytest.raises(SystemExit):
        sec.sec_getter("no contact")
    from kaibot.common import http

    seen = {}

    def fake(url, **kw):
        seen.update(kw)
        import gzip

        return http.Response(200, {"content-encoding": "gzip"}, gzip.compress(b"{}"))

    monkeypatch.setattr(http, "get", fake)
    assert sec.sec_getter("Kaibot research ops@example.com")(sec.TICKERS_URL) == b"{}"
    assert seen["source"] == "sec_edgar" and seen["headers"]["User-Agent"] == "Kaibot research ops@example.com"


# ---------------------------------------------------------------- ticker -> CIK


def _membership() -> pit.Membership:
    changes = [pit.Change(D(2016, 1, 1), ("GONE", "OLDC", "XYZ")),
               pit.Change(D(2019, 6, 3), ("BETA",), ("GONE", "OLDC")),
               pit.Change(D(2020, 1, 2), ("ACME",), ("XYZ",))]
    aliases = {"XYZ": [pit.Alias("XYZ", D(2020, 1, 1), "ACME")]}
    return pit.Membership(changes, aliases, [], D(2026, 11, 12))


NAMES = {"ACME": {"XYZ HOLDINGS"}, "OLDC": {"OLDCO INDUSTRIES"}, "BETA": set(), "GONE": set()}


def _resolve(tmp_path, get=None, overrides=None):
    get = get or FakeSec()
    cache = sec.Cache(tmp_path / "sub", get)
    tickers = sec.ticker_ciks(_doc("company_tickers.json"))
    lookup = lambda: sec.parse_cik_lookup((FIX / "cik-lookup-data.txt").read_text())  # noqa: E731
    return sec.resolve(_membership(), tickers, overrides or {}, cache.company, NAMES, lookup), cache, get


def test_ticker_table_and_lookup():
    t = sec.ticker_ciks(_doc("company_tickers.json"))
    assert t == {"ACME": "0000000101", "BETA": "0000000303", "OLDC": "0000000909"}
    lk = sec.parse_cik_lookup((FIX / "cik-lookup-data.txt").read_text())
    assert lk["OLDCO INDUSTRIES"] == ["0000000202"] and "OLDCO INDUSTRIES PENSION" in lk


def test_resolve_current_renamed_reused_and_missing(tmp_path):
    rows, _, get = _resolve(tmp_path)
    by = {r.company: r for r in rows}
    assert (by["ACME"].cik, by["ACME"].how, by["ACME"].verified) == ("0000000101", "ticker", True)
    assert (by["BETA"].cik, by["BETA"].how) == ("0000000303", "ticker")
    # OLDC is today another company's ticker (CIK 909, a different name): rejected, found by its IVV name
    assert (by["OLDC"].cik, by["OLDC"].how) == ("0000000202", "name")
    assert by["GONE"].cik == "" and by["GONE"].note == "no IVV issuer name"
    assert sum(u.endswith("CIK0000000909.json") for u in get.urls) == 1


def test_overrides_win_and_blank_means_checked(tmp_path):
    rows, _, _ = _resolve(tmp_path, overrides={"GONE": ("0000000202", "hand"), "BETA": ("", "no filer")})
    by = {r.company: r for r in rows}
    assert (by["GONE"].cik, by["GONE"].how) == ("0000000202", "override")
    assert by["BETA"].cik == "" and by["BETA"].how == "miss"
    assert sec.read_overrides() == {}  # the committed file starts empty (header only)


# ---------------------------------------------------------------- the table


def _table(tmp_path) -> pd.DataFrame:
    rows, cache, _ = _resolve(tmp_path)
    return sec.build(_membership(), rows, cache.load)


def test_build_columns_sessions_and_reports(tmp_path):
    df = _table(tmp_path)
    assert list(df.columns) == sec.COLUMNS
    acme = df[df["company"] == "ACME"].reset_index(drop=True)
    assert list(acme["symbol"]) == ["XYZ"] * 6  # point-in-time ticker (renamed to ACME only in 2020)
    first = acme.iloc[0]  # 2016-01-26 16:05 ET, after the close
    assert (first["session"], first["last_close_before"]) == (D(2016, 1, 27), D(2016, 1, 26))
    intraday = acme[acme["accession"] == "0000000101-16-000007"].iloc[0]  # 2016-10-25 11:00 ET
    assert (intraday["session"], intraday["last_close_before"]) == (D(2016, 10, 26), D(2016, 10, 24))
    assert list(acme["report"]) == [True, True, True, True, False, True]  # 27 Oct is a follow-up
    assert set(df["company"]) == {"ACME", "BETA", "OLDC"} and (df["source"] == sec.SOURCE).all()
    assert str(df["accepted_ts"].dt.tz) == "UTC"
    assert (df["session"].map(nyse.open_utc) > df["accepted_ts"].dt.to_pydatetime()).all()
    chk = sec.tz_check(df)
    assert chk["utc_reading_ok"] and chk["share_in_edgar_hours"] == 1.0


def test_tz_check_flags_eastern_times_labelled_utc(tmp_path):
    df = _table(tmp_path)
    shifted = df.assign(accepted_ts=df["accepted_ts"] - pd.Timedelta(hours=9))  # as if 21:05 ET were 21:05 UTC
    assert not sec.tz_check(shifted)["utc_reading_ok"]


# ---------------------------------------------------------------- the point-in-time reader


def _ann(tmp_path, calendar=None) -> pit_e.Announcements:
    return pit_e.Announcements(_table(tmp_path), calendar, _membership())


def test_never_returns_a_filing_before_it_was_accepted(tmp_path):
    a = _ann(tmp_path)
    t = utc("2016-01-26T21:05:00")
    before = a.next_known_announcement("ACME", t - timedelta(seconds=1))
    assert before is None or before.kind != "filed"  # no report known yet: no estimate either
    at = a.next_known_announcement("ACME", t)
    assert (at.kind, at.session, at.accepted_ts) == ("filed", D(2016, 1, 27), t)
    nxt = a.next_known_announcement("ACME", utc("2016-01-27T15:00:00"))  # the session opened: now an estimate
    assert nxt.kind == "estimate" and nxt.known_since == t and nxt.session == D(2016, 4, 26)
    assert a.last_known_announcement("ACME", t - timedelta(seconds=1)) is None
    assert a.last_known_announcement("ACME", t).accepted_ts == t


def test_symbol_forms(tmp_path):
    a = _ann(tmp_path)
    t = utc("2016-03-01T15:00:00")
    assert a.company("ACME", t) == "ACME" and a.company("XYZ", t) == "ACME"
    assert a.company("XYZ@2019-12-20", t) == "ACME" and a.company("NOPE", t) is None
    assert a.next_known_announcement("NOPE", t) is None
    with pytest.raises(ValueError):
        a.next_known_announcement("ACME", datetime(2016, 3, 1))


def _calendar() -> pd.DataFrame:
    rows = cal.parse(_doc("finnhub_calendar.json"), {"ACME", "BETA"}, utc("2026-11-12T23:00:00"))
    return pd.DataFrame(rows, columns=cal.COLUMNS)


def test_scheduled_dates_only_from_snapshots_already_taken(tmp_path):
    a = _ann(tmp_path, _calendar())
    before = a.next_known_announcement("ACME", utc("2026-11-12T22:59:59"))
    assert before is not None and before.kind == "estimate"
    k = a.next_known_announcement("ACME", utc("2026-11-13T01:00:00"))
    assert (k.kind, k.session, k.last_close_before, k.hour) == ("scheduled", D(2026, 11, 18), D(2026, 11, 17), "amc")
    b = a.next_known_announcement("BETA", utc("2026-11-13T01:00:00"))
    assert (b.session, b.last_close_before) == (D(2026, 11, 18), D(2026, 11, 17))  # before the open


def test_point_in_time_invariance(tmp_path):
    """For any decision time, the answer is the same when every filing accepted later and every snapshot taken
    later is deleted, and its evidence is never newer than the decision time."""
    table, calendar = _table(tmp_path), _calendar()
    full = pit_e.Announcements(table, calendar, _membership())
    rng = random.Random(7)
    lo, hi = utc("2015-12-01T00:00:00").timestamp(), utc("2026-12-31T00:00:00").timestamp()
    times = [datetime.fromtimestamp(rng.uniform(lo, hi), UTC) for _ in range(150)]
    times += [ts + timedelta(seconds=s) for ts in table["accepted_ts"].dt.to_pydatetime() for s in (-1, 0, 1)]
    times += [utc("2026-11-12T23:00:00") + timedelta(seconds=s) for s in (-1, 0, 1)]
    for t in times:
        cut = pit_e.Announcements(table[table["accepted_ts"] <= t], calendar[calendar["snapshot_ts"] <= t],
                                  _membership())
        for s in ("ACME", "OLDC", "BETA"):
            k = full.next_known_announcement(s, t)
            assert k == cut.next_known_announcement(s, t), (s, t)
            if k is not None:
                assert k.known_since <= t
                assert k.accepted_ts is None or k.accepted_ts <= t
            assert (full.known(s, t)["accepted_ts"] <= t).all()


def test_expected_report_rules():
    reps = [D(2016, 1, 26), D(2016, 4, 26), D(2016, 7, 26), D(2016, 10, 25), D(2017, 1, 24)]
    assert pit_e.expected_report(reps, D(2017, 1, 25)) == D(2017, 4, 25)  # a year after Q1 2016
    assert pit_e.expected_report(reps[:1], D(2016, 1, 27)) == D(2016, 4, 26)  # 91 days
    assert pit_e.expected_report(reps[:1], D(2016, 6, 1)) == D(2016, 7, 26)  # rolled a quarter
    assert pit_e.expected_report([], D(2016, 1, 1)) is None


# ---------------------------------------------------------------- forward calendar


def test_calendar_parse_and_chunked_snapshot():
    rows = cal.parse(_doc("finnhub_calendar.json"), None, utc("2026-11-12T23:00:00"))
    assert [r["symbol"] for r in rows] == ["ACME", "BETA", "NOTOURS"]
    assert rows[0]["hour"] == "amc" and rows[0]["eps_estimate"] == 1.2 and rows[1]["eps_estimate"] is None
    calls = []

    def get(url, params):
        calls.append(params)
        return (FIX / "finnhub_calendar.json").read_bytes()

    df = cal.snapshot(get, D(2026, 11, 12), {"ACME", "BETA"}, now=utc("2026-11-12T23:00:00"))
    assert len(calls) == 10 and calls[0] == {"from": "2026-11-12", "to": "2026-11-18"}
    assert calls[-1]["to"] == "2027-01-14" and len(df) == 2  # duplicates across chunks dropped
    with pytest.raises(ValueError):
        cal.parse({"nope": 1}, None, utc("2026-11-12T23:00:00"))


def test_calendar_unavailable_writes_nothing(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_earnings_dates as script

    def boom(token):
        def get(url, params):
            raise cal.Unavailable("finnhub calendar: HTTP 403")
        return get

    monkeypatch.setattr(cal, "finnhub_getter", boom)
    msg = script.run_snapshot(tmp_path, "token")
    assert "unavailable" in msg and not list(tmp_path.rglob("*.parquet"))
    assert "no FINNHUB_API_KEY" in script.run_snapshot(tmp_path, "")


# ---------------------------------------------------------------- coverage


def _quarterly(n_years=4, jitter=0, intraday=False) -> pd.DataFrame:
    rows = []
    rng = random.Random(3)
    for c in ("AAA", "BBB"):
        d0 = D(2016, 1, 26)
        for q in range(4 * n_years):
            d = nyse.sessions(d0 + timedelta(days=91 * q + rng.randint(-jitter, jitter)), D(2030, 1, 1))[0]
            ts = datetime.combine(d, datetime.min.time().replace(hour=11 if intraday else 16, minute=5), nyse.ET)
            rows.append({"company": c, "symbol": c, "accepted_ts": ts.astimezone(UTC), "report": True,
                         "session": nyse.first_session_opening_after(ts),
                         "last_close_before": nyse.last_session_closed_by(ts)})
    return pd.DataFrame(rows)


def test_coverage_numbers():
    tab = _quarterly(jitter=3)
    dates = nyse.sessions(D(2016, 1, 1), D(2020, 12, 31))
    darr = np.asarray(dates, dtype="datetime64[D]")
    ev = {c: list(np.searchsorted(darr, np.asarray(g["session"], dtype="datetime64[D]")))
          for c, g in tab.groupby("company")}
    ev["BBB"] = ev["BBB"][::2]  # the headline detector misses every second BBB report
    res = pit_e.coverage(tab, dates, blackout, ev)
    assert res["companies_with_reports"] == 2 and res["reports"] == 32
    assert res["sec_estimate"]["flat_before_report_share"] == 1.0
    h = res["headline_estimate"]
    assert h["detections_that_are_sec_reports"] == 1.0
    assert h["sec_reports_detected_within_1_session"] == 0.75
    assert 0 < res["sec_estimate"]["blackout_share_of_sessions"] < 0.5
    assert res["median_reports_per_company_year"] == pytest.approx(4.0, abs=0.2)
    assert "headline_estimate" not in pit_e.coverage(tab, dates, blackout, None)


# ---------------------------------------------------------------- the script, end to end with fake SEC


def test_script_fetch_build_and_offline_rebuild(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_earnings_dates as script

    get = FakeSec()
    monkeypatch.setattr(sec, "sec_getter", lambda ua: get)
    monkeypatch.setattr(script.Membership, "load", classmethod(lambda cls, *a, **k: _membership()))
    monkeypatch.setattr(sec, "company_names", lambda m: NAMES)
    (tmp_path / "sec").mkdir()
    (tmp_path / "sec" / "cik-lookup-data.txt").write_text((FIX / "cik-lookup-data.txt").read_text())
    meta = script.fetch_and_build(tmp_path, "Kaibot ops@example.com", refresh=False)
    assert meta["mapped"] == 3 and meta["misses"] == 1 and meta["rows"] == 11 and meta["tz_check"]["utc_reading_ok"]
    assert (tmp_path / "announcements.parquet").is_file() and (tmp_path / "cik_misses.csv").read_text().count("\n") == 2
    n = len(get.urls)
    meta2 = script.fetch_and_build(tmp_path, "Kaibot ops@example.com", refresh=False)
    assert len(get.urls) - n == 1 and meta2["rows"] == 11  # resumed: only company_tickers.json again
    assert script.offline_build(tmp_path)["rows"] == 11
    cov = script.run_coverage(tmp_path)
    assert cov["headline_part"].startswith("skipped") and cov["reports"] == 10


def test_report_events_feed_the_harness_calendar_point_in_time():
    tab = _quarterly(jitter=3)
    dates = nyse.sessions(D(2016, 1, 1), D(2020, 12, 31))
    cols = ["AAA", "SPY", "BBB"]
    ev = pit_e.report_events(tab, dates, cols)
    assert set(ev) == {"AAA", "BBB"} and len(ev["AAA"]) == 16
    hold, entry = calendar(np.asarray(dates, dtype="datetime64[D]"), cols, ev)
    assert not hold[:, 1].any() and hold[:, 0].any() and entry[:, 2].any()
    darr = np.asarray(dates, dtype="datetime64[D]")
    for t in (100, 400, 700):  # deleting every report not yet tradable at t leaves rows <= t unchanged
        cut = tab[np.asarray(tab["session"], dtype="datetime64[D]") <= darr[t]]
        h2, e2 = calendar(darr, cols, pit_e.report_events(cut, dates, cols))
        assert (h2[: t + 1] == hold[: t + 1]).all() and (e2[: t + 1] == entry[: t + 1]).all()
    for e, lcb in zip(ev["AAA"], tab[tab["company"] == "AAA"]["last_close_before"], strict=True):
        assert darr[e] > np.datetime64(lcb)  # tradable only after the last close before the release


def test_exact_matrix_is_the_night_before():
    tab = _quarterly(jitter=3)
    dates = nyse.sessions(D(2016, 1, 1), D(2020, 12, 31))
    darr = np.asarray(dates, dtype="datetime64[D]")
    hx, ex = pit_e.exact_matrix(tab, dates, ["AAA"], lead_sessions=3)
    first = np.searchsorted(darr, np.datetime64(tab["last_close_before"].iloc[0]))
    assert hx[:, 0].sum() == 16 and hx[first, 0] and ex[first - 3:first + 1, 0].all() and not ex[first - 4, 0]


def test_columns_map_store_keys_through_membership(tmp_path):
    tab = _table(tmp_path)
    dates = nyse.sessions(D(2016, 1, 1), D(2018, 12, 31))
    m = _membership()
    cols = [*m.keys("ACME"), "SPY", *m.keys("OLDC")]
    assert cols[0].startswith("ACME@") and cols[2].startswith("OLDC@")
    assert set(pit_e.report_events(tab, dates, cols, m)) == {cols[0], cols[2]}
    hold, _ = pit_e.exact_matrix(tab, dates, cols, m)
    assert hold.any(axis=0).tolist() == [True, False, True]
