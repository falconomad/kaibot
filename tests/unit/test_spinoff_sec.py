"""Spin-off ratios from SEC 8-K filings (story #298, shadow only): the patterns, the candidate filings, the
resumable fetch, one row per spin-off, point in time, the #262 check and the isolation from the live path.

Offline. Two kinds of fixtures under tests/fixtures/spinoff (see its README.md):
- recorded/: real filing lists from data.sec.gov (17 Nov 2026), trimmed to the 8-K rows around the #262 dates;
- synthetic/: fictional companies, shaped like EDGAR's files. They are NOT SEC filings: www.sec.gov, where the
  filing documents live, was not reachable when this was written.
"""

from __future__ import annotations

import ast
import csv
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from kaibot.common.ratelimit import load_specs
from kaibot.ingest import spinoff_sec as sp

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "spinoff"
SYN, REC = FIX / "synthetic", FIX / "recorded"
SRC = ROOT / "src" / "kaibot"
D = date
UA = "Kaibot research ops@example.com"
EVENTS = [sp.Event("ZZP", D(2026, 3, 2), "ZZN", 1 / 3), sp.Event("ZZQ", D(2026, 4, 1), "ZZO", 0.5),
          sp.Event("ZZQ", D(2026, 4, 1), "ZZC"), sp.Event("ZZX", D(2026, 5, 4), "ZZY"),
          sp.Event("ZZM", D(2026, 6, 1), "ZZW"), sp.Event("GONE", D(2026, 6, 1), "NEW")]


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


class FakeSec:
    """Serves tests/fixtures/spinoff/synthetic by URL and records every request."""

    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if "/Archives/edgar/data/" in url:
            acc, doc = url.split("/")[-2:]
            p = SYN / "archive" / acc / doc
        else:
            p = SYN / url.rsplit("/", 1)[-1]
        if not p.is_file():
            raise RuntimeError(f"HTTP 404 for {url}")
        return p.read_bytes()


def tickers() -> dict:
    return sp.ticker_table(json.loads((SYN / "company_tickers.json").read_text()))


# ---------------------------------------------------------------- the patterns


CTX = "The Board approved the distribution to effect the spin-off. "


@pytest.mark.parametrize("text,new,per,child", [
    ("one share of NewCo common stock for every three shares of Parent common stock held", 1, 3, "NEWCO"),
    ("One (1) share of NewCo common stock for every four (4) shares of Parent common stock.", 1, 4, "NEWCO"),
    ("0.5 shares of Otter Systems common stock for each share of Quill common stock outstanding", 0.5, 1,
     "OTTER SYSTEMS"),
    ("0.241917 shares of Wide Media Series A common stock for each share of Parent common stock,", 0.241917, 1,
     "WIDE MEDIA SERIES A"),
    ("1 ordinary share of NewCo plc for every 3 ordinary shares of Parent plc held", 1, 3, "NEWCO PLC"),
    ("for every two shares of Parent Class A common stock held, holders received one share of NewCo common stock.",
     1, 2, "NEWCO"),
    ("For each share of Parent common stock, shareholders will receive 2 shares of NewCo common stock.", 2, 1, "NEWCO"),
])
def test_ratio_statements(text, new, per, child):
    (s,) = sp.statements(CTX + text)
    assert (s.new_shares, s.parent_shares) == (new, per) and s.ratio == pytest.approx(new / per)
    assert sp.company_of(s.child) == child and s.quote in CTX + text


@pytest.mark.parametrize("text", [
    "Holders received one share of NewCo common stock for every three shares of Parent common stock.",  # no context
    CTX + "In the reverse split, one share of common stock for every ten shares of common stock.",  # one company
    CTX + "Each director has one vote for each share held.",
    CTX + "zero shares of NewCo common stock for every three shares of Parent common stock",
    CTX + "500 shares of NewCo common stock for every three shares of Parent common stock",  # out of range
    CTX + "one share of NewCo common stock. Later, Other Corp paid a dividend for every share of Parent stock.",
])
def test_what_is_not_a_ratio_statement(text):
    assert sp.statements(text) == []


def test_plain_text_reads_edgar_html():
    raw = (b"<html><head><style>p{margin:0}</style><script>var a='one share of X';</script></head><body><p>one&nbsp;"
           b"share of <b>NewCo</b> common&#160;stock<br/>for every three shares of Parent&#8217;s Group common stock"
           b"</p></body></html>")
    assert sp.plain_text(raw) == "one share of NewCo common stock for every three shares of Parent's Group common stock"


def test_stated_value_is_per_parent_share_only():
    t = ("In the distribution, one share of NewCo common stock for every three shares of Parent common stock. "
         "The value of the distribution was $14.25 per share of Parent common stock. "
         "NewCo closed at a value of $42.75 per share of NewCo common stock after the distribution. "
         "A dividend of $0.50 per share of Parent common stock was paid.")
    v, quote = sp.stated_value(t, "Parent common stock")
    assert v == 14.25 and quote.startswith("The value of the distribution")
    assert sp.stated_value(t.replace("$14.25", "$1,014.25"), "Parent common stock")[0] == 1014.25
    assert sp.stated_value(t, "Other common stock") is None  # the parent is named as in the ratio statement
    assert sp.stated_value("The value of the distribution was large.", "Parent common stock") is None


# ---------------------------------------------------------------- the parent's 8-K list


def _block(**over):
    b = {"accessionNumber": ["0000000001-26-000002", "0000000001-26-000001", "bad", "0000000001-26-000003"],
         "filingDate": ["2026-02-02", "2026-01-05", "2026-01-04", "2015-01-04"],
         "acceptanceDateTime": ["2026-02-02T12:00:00.000Z", "2026-01-05T22:00:00.000Z"] * 2,
         "form": ["8-K", "8-K/A", "8-K", "8-K"], "items": ["2.01,9.01", "8.01", "8.01", "8.01"],
         "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"]}
    b.update(over)
    return b


def test_parse_block_keeps_well_formed_8ks_only():
    (f,) = sp.parse_block(_block(), D(2016, 1, 1))  # the amendment, the bad accession and the 2015 row are dropped
    assert f.accession == "0000000001-26-000002" and f.accepted_ts == utc("2026-02-02T12:00:00")
    assert f.url("0000000007") == "https://www.sec.gov/Archives/edgar/data/7/000000000126000002/a.htm"
    assert sp.parse_block(_block(primaryDocument=["../x.htm", "b.htm", "c.htm", "d.htm"]), D(2016, 1, 1)) == []
    assert sp.parse_block(_block(acceptanceDateTime=["2026-02-02T12:00:00"] * 4), D(2016, 1, 1)) == []  # no zone
    with pytest.raises(ValueError, match="ragged"):
        sp.parse_block(_block(items=["8.01"]), D(2016, 1, 1))
    with pytest.raises(ValueError, match="schema"):
        sp.parse_block({"form": []}, D(2016, 1, 1))
    with pytest.raises(ValueError, match="wrong or missing cik"):
        sp.filings_from_submissions("0000000007", {"cik": "8", "filings": {"recent": _block()}}, {}, D(2016, 1, 1))


def test_candidates_window_items_and_limit():
    ex = D(2026, 3, 2)

    def f(n, ts, items="8.01"):
        return sp.Filing(f"0000000001-26-{n:06d}", "8-K", ts.date(), ts, items, "a.htm")

    o = sp.ex_open(ex)
    assert o == utc("2026-03-02T14:30:00")
    fl = [f(1, o - timedelta(days=121)), f(2, o - timedelta(days=119)), f(3, o - timedelta(days=30), "2.02,9.01"),
          f(4, o - timedelta(days=30), "5.02, 7.01"), f(5, o + timedelta(days=6)), f(6, o + timedelta(days=8))]
    assert [x.accession[-1] for x in sp.candidates(fl, ex)] == ["2", "4", "5"]
    assert [x.accession[-1] for x in sp.candidates(fl, ex, limit=2)] == ["4", "5"]  # nearest kept, oldest first


def test_exhibits_from_index():
    idx = json.loads((SYN / "archive" / "999999900126000002" / "index.json").read_text())
    assert sp.exhibits_from_index(idx, "zzp-20260115.htm") == ["zzp-ex99_1.htm"]
    assert sp.exhibits_from_index({"directory": {"item": [{"name": "../ex99.htm"}, {"name": 7}]}}, "a.htm") == []
    assert sp.exhibits_from_index("nope", "a.htm") == []


# ---------------------------------------------------------------- one row per spin-off (synthetic filings)


@pytest.fixture
def built(tmp_path):
    get = FakeSec()
    cache = sp.Cache(tmp_path, get)
    rows, misses = sp.collect(EVENTS, cache, tickers())
    return get, cache, sp.table(rows), misses, tmp_path


def test_one_row_per_spin_off_from_the_earliest_filing_that_states_the_ratio(built):
    get, cache, df, misses, _ = built
    assert list(df.columns) == sp.COLUMNS and len(df) == 3
    r = df[df["new_symbol"] == "ZZN"].iloc[0]
    assert r["parent"] == "ZZP" and r["ex_date"] == D(2026, 3, 2)
    assert (r["new_shares"], r["parent_shares"]) == (1, 3) and r["ratio"] == pytest.approx(1 / 3)
    # the press release of 15 January, not the completion 8-K of the ex-date: the first time the ratio was public
    assert r["available_ts"] == pd.Timestamp("2026-01-15T21:30:00Z") and r["accession"] == "9999999001-26-000002"
    assert r["filing_url"] == "https://www.sec.gov/Archives/edgar/data/9001/999999900126000002/zzp-ex99_1.htm"
    assert r["doc"] == "zzp-ex99_1.htm" and "for every three shares of Zeta Parent common stock" in r["quote"]
    assert r["known_before_ex"] and r["ratio_filings"] == 2 and r["filings_read"] == 3 and r["match"] == "only_ratio"
    assert r["alpaca_ratio"] == pytest.approx(1 / 3) and r["source"] == "sec_edgar_8k_spinoff"
    assert r["cik"] == "0000009001"
    # the value is stated only in the later filing and carries that filing's time
    assert r["value_per_parent_share"] == 14.25 and r["value_available_ts"] == pd.Timestamp("2026-03-02T11:05:00Z")


def test_two_spin_offs_on_one_day_are_told_apart_by_the_new_company_name(built):
    df = built[2]
    o, c = (df[df["new_symbol"] == s].iloc[0] for s in ("ZZO", "ZZC"))
    assert o["ratio"] == 0.5 and c["ratio"] == 1.0 and o["match"] == c["match"] == "child_name"
    assert o["available_ts"] == c["available_ts"] == pd.Timestamp("2026-03-11T22:15:00Z")
    assert math.isnan(o["value_per_parent_share"]) and pd.isna(o["value_available_ts"])
    assert math.isnan(c["alpaca_ratio"])


def test_no_row_without_exactly_one_ratio(built):
    misses = {m["parent"]: m["reason"] for m in built[3]}
    assert set(misses) == {"ZZX", "ZZM", "GONE"}
    assert misses["ZZX"].startswith("several ratios stated: 1 per 4 (YARROW LABS); 1 per 5 (YARROW LABS)")
    assert misses["ZZM"] == "no ratio statement in the filings read (1 of 1 candidate 8-Ks read)"
    assert "not in today's SEC ticker table" in misses["GONE"]
    two = [(None, {"new_shares": 1, "parent_shares": n, "child": f"{c} stock"}) for n, c in ((2, "A"), (3, "B"))]
    assert sp.choose([], "x")[2] and sp.choose(two)[0] == [] and sp.choose(two, "B Corp")[0] == two[1:]


def test_one_stated_ratio_is_not_given_to_a_sibling_spin_off_of_the_same_day(tmp_path):
    """A parent with two spin-offs on one ex-date whose filings state only one company's ratio: the other company
    gets no row (before, both got the same ratio as "only_ratio")."""
    one = [(None, {"new_shares": 1, "parent_shares": 2, "child": "Otter Systems common stock"})]
    assert sp.choose(one, "")[1] == sp.choose(one, "Crane Devices Inc")[1] == "only_ratio"  # no sibling: as before
    assert sp.choose(one, "Otter Systems Inc", siblings=True)[1:] == ("child_name", "")
    hit, how, why = sp.choose(one, "Crane Devices Inc", siblings=True)
    assert hit == [] and "several spin-offs that day" in why and "1 per 2 (OTTER SYSTEMS)" in why
    assert sp.choose(one, "", siblings=True)[0] == []  # no name to go by

    def only_otter(url):  # the synthetic 8-K with the sentence about the second company removed
        body = FakeSec()(url)
        return body.split(b" and one share of ")[0] if url.endswith("zzq-20260311.htm") else body

    assert b" and one share of " in FakeSec()(sp.ARCHIVE_URL.format(cik=9003, acc="999999900326000004",
                                                                   doc="zzq-20260311.htm"))
    rows, misses = sp.collect(EVENTS[1:3], sp.Cache(tmp_path, only_otter), tickers())
    assert [(r["new_symbol"], r["ratio"], r["match"]) for r in rows] == [("ZZO", 0.5, "child_name")]
    assert [m["new_symbol"] for m in misses] == ["ZZC"] and "several spin-offs that day" in misses[0]["reason"]


def test_available_ts_is_the_acceptance_time_never_the_ex_date(built):
    df = built[2]
    accepted = {}
    for p in SYN.glob("CIK*.json"):
        b = json.loads(p.read_text())["filings"]["recent"]
        accepted.update(zip(b["accessionNumber"], b["acceptanceDateTime"], strict=True))
    for r in df.itertuples():
        assert r.available_ts == pd.Timestamp(accepted[r.accession])
        assert r.available_ts.tz is not None and r.available_ts.date() != r.ex_date
        assert r.available_ts.time() != datetime.min.time()  # a clock time, not a date


def test_known_at_is_point_in_time(built):
    df = built[2]
    assert len(sp.known_at(df, utc("2026-01-15T21:29:59"))) == 0
    a = sp.known_at(df, utc("2026-01-15T21:30:00"))
    assert list(a["new_symbol"]) == ["ZZN"] and math.isnan(a["value_per_parent_share"].iloc[0])
    assert pd.isna(a["value_available_ts"].iloc[0])
    b = sp.known_at(df, utc("2026-03-02T11:05:00"))
    assert b["value_per_parent_share"].iloc[0] == 14.25 and len(sp.known_at(df, utc("2026-03-11T22:15:00"))) == 3
    # deleting every later row changes nothing a reader at t sees
    for t in (utc("2026-01-20T00:00:00"), utc("2026-03-05T00:00:00"), utc("2027-01-01T00:00:00")):
        pd.testing.assert_frame_equal(sp.known_at(df, t), sp.known_at(df[df["available_ts"] <= pd.Timestamp(t)], t))
    with pytest.raises(ValueError):
        sp.known_at(df, datetime(2026, 3, 1))
    assert df["value_per_parent_share"].notna().sum() == 1  # known_at works on a copy


def test_fetch_is_frugal_resumable_and_works_offline(built):
    get, cache, df, misses, root = built
    names = [u.rsplit("/", 1)[-1] for u in get.urls]
    assert cache.requests == len(get.urls) == 14
    assert names.count("index.json") == 2  # only when the primary document states no ratio and there are exhibits
    assert "zzp-20260210.htm" not in names  # an earnings 8-K (Item 2.02) is not a candidate
    assert "zzp-ex10_1.htm" not in names and "R1.htm" not in names and "zzm-ex2_1.htm" not in names
    assert all(u.startswith(("https://data.sec.gov/submissions/", "https://www.sec.gov/Archives/edgar/data/"))
               for u in get.urls)
    again = sp.Cache(root, get)
    rows2, misses2 = sp.collect(EVENTS, again, tickers())
    assert again.requests == 0 and len(get.urls) == 14 and misses2 == misses
    pd.testing.assert_frame_equal(sp.table(rows2), df)
    offline = sp.Cache(root, None)
    pd.testing.assert_frame_equal(sp.table(sp.collect(EVENTS, offline, tickers())[0]), df)
    empty = sp.collect(EVENTS[:1], sp.Cache(root / "empty", None), tickers())[1]
    assert empty[0]["reason"].startswith("no filing list")


def test_filing_lists_reach_back_only_as_far_as_the_spin_off_needs(tmp_path):
    old = "CIK0000000007-submissions-001.json"
    docs = {"CIK0000000007.json": {"cik": "7", "name": "Seven", "filings": {
                "recent": _block(), "files": [{"name": old, "filingFrom": "2010-01-01", "filingTo": "2019-12-31"}]}},
            old: _block(accessionNumber=["0000000001-19-000009", "x", "y", "z"], filingDate=["2019-06-03"] * 4)}
    urls = []

    def get(url):
        urls.append(url.rsplit("/", 1)[-1])
        return json.dumps(docs[urls[-1]]).encode()

    c = sp.Cache(tmp_path, get)
    assert len(c.filings("0000000007", D(2025, 11, 1))) == 1 and urls == ["CIK0000000007.json"]
    assert len(c.filings("0000000007", D(2025, 12, 1))) == 1 and len(urls) == 1  # cached
    assert len(c.filings("0000000007", D(2019, 1, 1))) == 2 and urls[1:] == ["CIK0000000007.json", old]
    assert len(sp.Cache(tmp_path, None).filings("0000000007", D(2018, 1, 1))) == 2  # offline: what is cached
    again = sp.Cache(tmp_path, get)
    again.filings("0000000007", D(2025, 1, 1), refresh=True)
    again.filings("0000000007", D(2025, 1, 1), refresh=True)  # a parent with two spin-offs is re-read once
    assert len(urls) == 5 and again.requests == 2 and urls[-1] == old  # and it keeps reaching back to 2019
    two = [sp.Event("SVN", D(2026, 2, 20), "A", cik="0000000007"),
           sp.Event("SVN", D(2019, 7, 1), "B", cik="0000000007")]
    n = len(urls)
    sp.collect(two, sp.Cache(tmp_path / "x", lambda u: get(u) if "submissions" in u else b""), {})
    assert urls[n:n + 2] == ["CIK0000000007.json", old]  # one read that covers the older spin-off too


def test_an_unreadable_filing_is_retried_next_run(tmp_path):
    get = FakeSec()

    def flaky(url):
        if url.endswith("zzp-ex99_1.htm"):
            raise RuntimeError("HTTP 503")
        return get(url)

    cache = sp.Cache(tmp_path, flaky)
    rows, _ = sp.collect(EVENTS[:1], cache, tickers())
    assert "9999999001-26-000002" in cache.errors and not (tmp_path / "filings" / "9999999001-26-000002.json").exists()
    assert rows[0]["available_ts"] == utc("2026-03-02T11:05:00")  # what could be read; build.json lists the error
    rows, _ = sp.collect(EVENTS[:1], sp.Cache(tmp_path, get), tickers())
    assert rows[0]["available_ts"] == utc("2026-01-15T21:30:00")


def test_sec_getter_requires_a_contact_and_uses_the_shared_sec_budget(monkeypatch):
    with pytest.raises(SystemExit):
        sp.sec_getter("no contact")
    from kaibot.common import http

    seen = []

    def fake(url, **kw):
        seen.append(kw)
        return http.Response(200, {}, b"{}")

    monkeypatch.setattr(http, "get", fake)
    get = sp.sec_getter(UA)
    assert get(sp.TICKERS_URL) == b"{}" and get(sp.SUBMISSIONS_URL.format(name="CIK0000000001.json")) == b"{}"
    assert all(k["source"] == "sec_edgar" and k["headers"]["User-Agent"] == UA for k in seen)
    assert seen[0]["max_bytes"] == sp.MAX_DOC_BYTES < seen[1]["max_bytes"]


def test_sources_yaml_lists_the_source_under_the_sec_budget():
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())["fundamentals_filings"]
    entry = cfg["sec_spinoff_8k"]
    assert "count against sec_edgar" in entry["note"] and "never in the live path" in entry["note"]
    assert not any(k.startswith("budget_") for k in entry)  # no budget of its own: the shared one applies
    assert sp.BUDGET == "sec_edgar" and not load_specs()["sec_edgar"].unlimited


# ---------------------------------------------------------------- seeds


def test_events_from_the_daily_store_actions():
    raw = {"source_symbol": "ZZZ", "new_symbol": "zzn", "new_rate": 0.25, "source_rate": 1}
    df = pd.DataFrame([
        {"key": "ZZZ", "symbol": "ZZZ", "type": "spin_off", "ex_date": D(2026, 10, 28), "raw": json.dumps(raw)},
        {"key": "ZZZ", "symbol": "ZZZ", "type": "spin_off", "ex_date": D(2026, 10, 28), "raw": json.dumps(raw)},
        {"key": "OLD@2020-01-02", "symbol": "OLD", "type": "spin_off", "ex_date": D(2019, 5, 1), "raw": "not json"},
        {"key": "ZZZ", "symbol": "ZZZ", "type": "cash_dividend", "ex_date": D(2026, 10, 22), "raw": "{}"},
    ])
    assert sp.events_from_actions(df) == [sp.Event("OLD", D(2019, 5, 1)), sp.Event("ZZZ", D(2026, 10, 28), "ZZN", 0.25)]
    assert sp.events_from_actions(None) == [] and sp.events_from_actions(df.iloc[:0]) == []


def test_the_262_list_names_every_spin_off_of_the_issue():
    ev = sp.read_events(sp.CHECK_262)
    assert len(ev) == 15 and all(e.cik for e in ev)
    assert {e.parent for e in ev} == {"GE", "MMM", "T", "EXC", "DHR", "LEN", "HON", "CMCSA", "SPGI", "DD", "BDX", "FDX",
                                      "APTV"}


# ---------------------------------------------------------------- recorded EDGAR filing lists (real)


def test_recorded_edgar_lists_have_a_candidate_8k_for_every_262_spin_off():
    """Real data.sec.gov filing lists (trimmed): every spin-off of #262 has 8-Ks to read, and most parents filed an
    8-K with Item 2.01 or 8.01 that EDGAR accepted before the ex-date opened. The documents themselves could not
    be recorded (www.sec.gov not reachable), so the ratios of these filings are NOT checked here."""
    found, before = 0, 0
    for e in sp.read_events(sp.CHECK_262):
        doc = json.loads((REC / f"CIK{e.cik}.json").read_text())
        assert e.parent in doc["tickers"]
        cands = sp.candidates(sp.filings_from_submissions(e.cik, doc, {}, D(2016, 1, 1)), e.ex_date)
        found += bool(cands)
        before += any(f.accepted_ts < sp.ex_open(e.ex_date) for f in cands)
        assert 1 <= len(cands) <= sp.MAX_FILINGS, e
    assert found == 15 and before == 15
    doc = json.loads((REC / "CIK0000040545.json").read_text())
    ge = sp.filings_from_submissions("0000040545", doc, {}, D(2016, 1, 1))
    done = next(f for f in ge if f.accession == "0001193125-23-001157")  # GE's 8-K on the GE HealthCare ex-date
    assert done.accepted_ts == utc("2023-01-04T11:29:07") and "2.01" in done.items
    assert done.accepted_ts < sp.ex_open(D(2023, 1, 4))  # 06:29 New York: before that day's open
    assert done.url("0000040545") == "https://www.sec.gov/Archives/edgar/data/40545/000119312523001157/d431727d8k.htm"


# ---------------------------------------------------------------- the check against Alpaca


def test_compare_with_alpacas_ratio_and_close_factor(built):
    rows = built[2].to_dict("records")
    ref = [{"parent": "ZZP", "ex_date": "2026-03-02", "alpaca_close_factor": "0.875"},
           {"parent": "ZZQ", "ex_date": "2026-04-01", "alpaca_close_factor": ""},
           {"parent": "ZZX", "ex_date": "2026-05-04", "alpaca_close_factor": "0.9"}]
    px = {("ZZP", D(2026, 3, 2)): 100.0, ("ZZN", D(2026, 3, 2)): 42.75}
    a, b, c = sp.compare(rows, ref, lambda s, d: px.get((s, d), math.nan))
    assert a["found"] and a["ratio_agrees"] and a["known_before_ex"]
    assert a["sec_implied_factor"] == pytest.approx(100 / (100 + 42.75 / 3)) and a["agrees"]  # 0.8753 against 0.875
    assert a["factor_diff"] == pytest.approx(100 / 114.25 - 0.875)
    assert b["found"] and b["ratio_agrees"] in (True, None) and b["agrees"] is None  # no price, no factor
    assert not c["found"] and c["agrees"] is None and math.isnan(c["sec_ratio"])
    off = sp.compare(rows, ref[:1], lambda s, d: {"ZZP": 100.0, "ZZN": 90.0}[s])[0]
    assert off["agrees"] is False and off["factor_diff"] == pytest.approx(100 / 130 - 0.875)
    assert sp.compare(rows, ref[:1])[0]["agrees"] is None and math.isnan(sp.implied_factor(0, 1, 1))


@pytest.mark.parametrize("sec,alpaca,same", [
    (1 / 3, 0.33333, True),  # Alpaca's GE / GEHC and EXC / CEG records (read 17 Nov 2026): five decimals
    (1 / 3, 0.333333333, True), (0.241917, 0.24192, True), (0.25, 0.25, True), (0.04, 0.04, True),
    (1 / 3, 0.3333, False), (0.25, 0.2, False), (0.5, 0.51, False), (0.135343, 0.135443, False),
])
def test_ratio_equals_alpacas_up_to_its_five_decimals(sec, alpaca, same):
    assert sp.same_ratio(sec, alpaca) is same
    row = {"parent": "P", "ex_date": "2026-01-02", "ratio": sec, "alpaca_ratio": alpaca, "available_ts": "",
           "known_before_ex": True, "filing_url": "", "new_symbol": ""}
    assert sp.compare([row], [{"parent": "P", "ex_date": "2026-01-02"}])[0]["ratio_agrees"] is same


# ---------------------------------------------------------------- the script, end to end with fake SEC


def _script():
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_spinoffs as script

    return script


def test_script_fetch_build_offline_rebuild_and_check(tmp_path, monkeypatch, capsys):
    script = _script()
    get = FakeSec()
    monkeypatch.setattr(sp, "sec_getter", lambda ua: get)
    data, daily = tmp_path / "spinoffs", tmp_path / "daily"
    data.mkdir()
    meta = script.fetch_and_build(data, UA, EVENTS)
    assert meta["events"] == 6 and meta["rows"] == 3 and meta["misses"] == 3 and meta["known_before_ex"] == 3
    assert meta["with_value"] == 1 and meta["with_alpaca_ratio"] == 2 and meta["ratio_equals_alpaca"] == 2
    assert meta["requests_this_run"] == 15 and meta["errors"] == {}  # 14 + today's ticker table
    df = pd.read_parquet(data / "spinoffs.parquet")
    assert list(df.columns) == sp.COLUMNS and str(df["available_ts"].dtype).endswith("UTC]")
    assert (data / "misses.csv").read_text().count("\n") == 4
    assert script.fetch_and_build(data, UA, EVENTS)["requests_this_run"] == 1  # resumed: only the ticker table
    assert script.offline_build(data, EVENTS)["rows"] == 3
    pd.testing.assert_frame_equal(pd.read_parquet(data / "spinoffs.parquet"), df)
    lines = script.run_check(data, daily)  # no daily store here: found / not found only
    assert len(lines) == 15 and not any(x["found"] for x in lines) and (data / "check_262.csv").is_file()
    script._print_check(lines)
    assert "0 of 15 spin-offs" in capsys.readouterr().out
    script.status(data)
    assert "cached filings: 7" in capsys.readouterr().out


def test_script_seeds(tmp_path, monkeypatch):
    script = _script()
    assert script.seeds(tmp_path / "none", None, False) == []  # no store on this machine
    listed = script.seeds(tmp_path / "none", None, True)
    assert len(listed) == 15 and listed[0] == sp.Event("EXC", D(2022, 2, 2), "", None, "0001109357")
    store = [sp.Event("GE", D(2023, 1, 4), "GEHC", 1 / 3), sp.Event("ZZQ", D(2026, 4, 1), "ZZO", 0.5),
             sp.Event("ZZQ", D(2026, 4, 1), "ZZC", 1.0)]
    monkeypatch.setattr(script, "store_events", lambda daily: store)
    assert script.seeds(tmp_path, None, False) == store
    ge = [e for e in script.seeds(tmp_path, None, True) if e.parent == "GE"]
    assert ge == [sp.Event("GE", D(2023, 1, 4), "GEHC", 1 / 3, "0000040545"), sp.Event("GE", D(2024, 4, 2), "", None,
                                                                                      "0000040545")]
    f = tmp_path / "ev.csv"
    with f.open("w", newline="") as fh:
        csv.writer(fh).writerows([["parent", "ex_date", "new_symbol"], ["zzq", "2026-04-01", ""],
                                  ["ZZQ", "2026-04-01", "ZZC"], ["ZZP", "2026-03-02", "ZZN"]])
    got = script.seeds(tmp_path, str(f), False)
    assert got == [store[1], store[2], store[2], sp.Event("ZZP", D(2026, 3, 2), "ZZN")]


def test_script_main_needs_seeds_and_a_user_agent(tmp_path, monkeypatch, capsys):
    script = _script()
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    base = ["fetch_spinoffs.py", "--data", str(tmp_path / "s"), "--daily", str(tmp_path / "d")]
    monkeypatch.setattr(sys, "argv", base)
    assert script.main() == 1 and "No spin-offs to look up" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", [*base, "--check262"])
    assert script.main() == 1 and "Missing SEC_USER_AGENT" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", [*base, "--check262", "--build"])  # offline with an empty cache: all misses
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "Spin-offs 15: 0 with a ratio" in out and "0 of 15 spin-offs" in out


# ---------------------------------------------------------------- shadow only: the live path never reads it

TRADING = ["engine", "features", "strategies", "risk", "broker", "agents", "replay", "evaluation", "news"]


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_trading_code_never_reads_the_spin_off_rows():
    offenders = []
    for pkg in TRADING:
        for f in (SRC / pkg).rglob("*.py"):
            text = f.read_text()
            if (any("spinoff_sec" in m for m in _imports(f)) or "spinoff_sec" in text or "data/spinoffs" in text
                    or "spinoffs.parquet" in text or "fetch_spinoffs" in text):
                offenders.append(str(f.relative_to(SRC)))
    assert offenders == []
    mine = _imports(SRC / "ingest" / "spinoff_sec.py")
    assert not any(m.startswith(f"kaibot.{p}") for m in mine for p in TRADING)  # and it pulls none of them in
