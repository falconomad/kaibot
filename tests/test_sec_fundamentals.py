"""SEC XBRL fundamentals store (scripts/fetch_sec_fundamentals.py, kaibot.ingest.sec_fundamentals,
kaibot.data.fundamentals). Recorded / hand-made fixtures only: no network in CI."""

from __future__ import annotations

import gzip
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kaibot.common import http
from kaibot.data.fundamentals import Fundamentals
from kaibot.ingest import sec_fundamentals as sf
from kaibot.ingest.earnings_sec import Mapping_
from kaibot.ingest.sp500_pit import Alias, Change, Membership

FIX = Path(__file__).parent / "fixtures" / "sec_companyfacts" / "CIK0000000001.json"
CIK = "0000000001"
ROOT = Path(__file__).resolve().parents[1]


def _script():
    path = ROOT / "scripts" / "fetch_sec_fundamentals.py"
    spec = importlib.util.spec_from_file_location("fetch_sec_fundamentals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doc():
    return json.loads(FIX.read_text())


@pytest.fixture
def store(tmp_path):
    t, _ = sf.parse_companyfacts(_doc(), CIK)
    sf.merge_into_store(tmp_path, CIK, t)
    return Fundamentals(tmp_path)


def _val(df, metric, start, end):
    r = df[(df["metric"] == metric) & (df["period_start"] == start) & (df["period_end"] == end)]
    assert len(r) <= 1
    return None if r.empty else r.iloc[0]


# ---------------------------------------------------------------- parsing


def test_parse_keeps_every_filed_version_and_skips_bad_rows():
    t, rep = sf.parse_companyfacts(_doc(), CIK)
    df = t.to_pandas()
    assert rep.rows == 12 and rep.bad == 4 and rep.other_units == 1 and rep.entity == "EXAMPLE CORP"
    q1 = df[(df.metric == "revenues") & (df.concept == "Revenues") & (df.period_end == date(2019, 3, 31))]
    assert sorted(q1.value) == [95.0, 100.0]  # the original and the restatement; the duplicate row merged
    assert (df.available_date == df.filed + timedelta(days=1)).all()
    assert set(df.unit) == {"USD", "shares"} and "AccountsPayableCurrent" not in set(df.concept)
    assert "frame" not in df.columns  # assigned with hindsight by SEC: never stored
    shares = df[df.metric == "shares_outstanding"]
    assert shares.period_start.isna().all() and shares.duration_days.isna().all()


def test_parse_refuses_a_document_for_another_company():
    with pytest.raises(ValueError, match="CIK"):
        sf.parse_companyfacts(_doc(), "2")
    with pytest.raises(ValueError, match="schema"):
        sf.parse_companyfacts({"cik": 1}, CIK)


def test_store_never_drops_a_version_and_is_idempotent(tmp_path):
    t, _ = sf.parse_companyfacts(_doc(), CIK)
    n, added = sf.merge_into_store(tmp_path, CIK, t)
    assert (n, added) == (12, 12)
    assert sf.merge_into_store(tmp_path, CIK, t) == (12, 0)
    doc = _doc()  # a later download without the original Q1 2019 version: it stays in the store
    doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = doc["facts"]["us-gaap"]["Revenues"]["units"]["USD"][2:]
    t2, _ = sf.parse_companyfacts(doc, CIK)
    assert sf.merge_into_store(tmp_path, CIK, t2) == (12, 0)


def test_gunzip_is_bounded():
    bomb = gzip.compress(b"0" * 1_000_000)
    assert sf.gunzip_bounded(bomb, 2_000_000) == b"0" * 1_000_000
    with pytest.raises(ValueError):
        sf.gunzip_bounded(bomb, 1000)


# ---------------------------------------------------------------- point in time


def test_restatement_is_visible_only_from_the_day_after_it_was_filed(store):
    q1 = (date(2019, 1, 1), date(2019, 3, 31))
    assert _val(store.as_of(date(2019, 5, 1)), "revenues", *q1) is None  # filed that day: not yet usable
    assert _val(store.as_of(date(2019, 5, 2)), "revenues", *q1).value == 100
    assert _val(store.as_of(date(2020, 5, 1)), "revenues", *q1).value == 100  # restated on 1 May 2020
    assert _val(store.as_of(date(2020, 5, 2)), "revenues", *q1).value == 95
    fy = (date(2019, 1, 1), date(2019, 12, 31))
    assert _val(store.as_of(date(2020, 6, 15)), "revenues", *fy).value == 420  # 10-K
    assert _val(store.as_of(date(2020, 6, 16)), "revenues", *fy).value == 410  # 10-K/A


def test_preferred_concept_wins_and_a_fallback_fills_gaps(store):
    a = store.as_of(date(2019, 9, 1))
    assert _val(a, "revenues", date(2019, 1, 1), date(2019, 3, 31)).concept == "Revenues"  # not the 99
    q2 = _val(a, "revenues", date(2019, 4, 1), date(2019, 6, 30))
    assert q2.concept == "RevenueFromContractWithCustomerExcludingAssessedTax" and q2.value == 105


def test_latest_by_period_kind(store):
    assert store.latest(date(2019, 9, 1), "revenues", "Q").iloc[0].value == 105
    assert store.latest(date(2020, 3, 1), "revenues", "FY").iloc[0].value == 420
    assert store.latest(date(2020, 3, 1), "total_assets", "instant").iloc[0].value == 1200
    assert store.latest(date(2019, 5, 1), "total_assets", "instant").empty
    assert store.latest(date(2020, 5, 2), "shares_outstanding", "instant").iloc[0].value == 4_900_000
    assert len(store.history(CIK, "revenues")) == 7


@settings(max_examples=150, deadline=None)
@given(st.dates(min_value=date(2018, 1, 1), max_value=date(2021, 12, 31)))
def test_leakage_nothing_is_visible_before_the_day_after_filing(d):
    t, _ = sf.parse_companyfacts(_doc(), CIK)
    f = Fundamentals("/nonexistent")
    f._facts = t.to_pandas()
    a = f.as_of(d)
    assert (a["filed"] < d).all() and (a["available_date"] <= d).all()
    # and each visible value is the latest-filed preferred version among those filed before d
    allv = f._facts[f._facts["filed"] < d]
    for _, r in a.iterrows():
        same = allv[(allv.metric == r.metric) & (allv.period_end == r.period_end)
                    & (allv.period_start.fillna(date.min) == (r.period_start or date.min))]
        best = same[same.priority == same.priority.min()]
        assert r.filed == best.filed.max()


def test_every_version_is_hidden_on_its_filing_day_and_shown_the_next(store):
    for d in sorted(set(store.facts.filed)):
        assert (store.as_of(d)["filed"] < d).all()
        nxt = store.as_of(d + timedelta(days=1))
        filed_d = store.facts[store.facts.filed == d]
        # each version filed on d is visible the next day unless a preferred concept covers the same period
        for _, r in filed_d.iterrows():
            same = nxt[(nxt.metric == r.metric) & (nxt.period_end == r.period_end)]
            assert not same.empty and same.iloc[0].priority <= r.priority


# ---------------------------------------------------------------- ticker <-> CIK


def _membership():
    changes = [Change(date(2016, 1, 4), ("FB", "XOM", "AAA")), Change(date(2018, 6, 1), ("BBB",), ("AAA",)),
               Change(date(2022, 6, 9), ("META",), ("FB",))]
    aliases = {"FB": [Alias("FB", date(2022, 6, 8), "META")]}
    return Membership(changes, aliases, (), date(2026, 11, 24))


def test_ticker_spells_follow_renames_and_membership(tmp_path):
    m = _membership()
    mapping = [Mapping_("META", "1326801", "ticker", True), Mapping_("AAA", "77", "override", True),
               Mapping_("XOM", "", "miss", False)]
    sf.write_ciks(tmp_path, sf.ticker_spells(m, mapping))
    f = Fundamentals(tmp_path)
    assert f.cik_for("FB", date(2020, 1, 1)) == "0001326801"
    assert f.cik_for("META", date(2020, 1, 1)) is None
    assert f.cik_for("META", date(2023, 1, 1)) == "0001326801" and f.cik_for("FB", date(2023, 1, 1)) is None
    assert f.ticker_for("0001326801", date(2022, 6, 8)) == "FB"
    assert f.ticker_for("0001326801", date(2022, 6, 9)) == "META"
    assert f.cik_for("AAA", date(2018, 5, 31)) == "0000000077" and f.cik_for("AAA", date(2018, 6, 1)) is None
    assert f.cik_for("XOM", date(2020, 1, 1)) is None  # no CIK found: no row


# ---------------------------------------------------------------- the fetch script


def test_user_agent_is_required_and_read_alone(tmp_path):
    mod = _script()
    with pytest.raises(SystemExit, match="KAIBOT_SEC_USER_AGENT"):
        mod.user_agent(None, {})
    with pytest.raises(SystemExit):
        mod.user_agent(None, {"KAIBOT_SEC_USER_AGENT": "no contact"})
    env = tmp_path / ".env"
    env.write_text('ALPACA_SECRET=zzz\nexport KAIBOT_SEC_USER_AGENT="Test Person test@example.com"  # sec\n')
    assert mod.user_agent(str(env), {}) == "Test Person test@example.com"
    assert mod.user_agent(str(env), {"KAIBOT_SEC_USER_AGENT": "Other other@example.org"}) == "Other other@example.org"


def _fake_http(monkeypatch, status=200, calls=None):
    body = gzip.compress(FIX.read_bytes())

    def fake_get(url, *, source, headers, **kw):
        assert source == "sec_edgar" and url.startswith("https://data.sec.gov/api/xbrl/companyfacts/CIK")
        assert headers["User-Agent"] == "Test Person test@example.com"
        if calls is not None:
            calls.append(url)
        if status == 403:
            raise http.HttpError(403, url, "Forbidden")
        if "CIK0000000001" not in url:
            return http.Response(404, {}, b"")
        return http.Response(200, {"content-encoding": "gzip"}, body)

    monkeypatch.setattr(http, "get", fake_get)


def _earnings_map(data: Path):
    (data / "earnings").mkdir(parents=True)
    (data / "earnings" / "cik_map.csv").write_text(
        "company,cik,how,verified,note\nAAPL,1,ticker,1,\nMSFT,789019,ticker,1,\nGONE,,miss,0,x\n")


def test_fetch_end_to_end_with_a_recorded_response(tmp_path, monkeypatch):
    mod = _script()
    calls: list[str] = []
    _fake_http(monkeypatch, calls=calls)
    _earnings_map(tmp_path)
    meta = mod.fetch(tmp_path, "Test Person test@example.com", refresh=False, max_age_days=7, limit=None)
    assert meta["requests_this_run"] == 2 and meta["no_facts"] == ["0000789019"] and meta["errors"] == {}
    assert meta["rows_parsed"] == 12
    f = Fundamentals(tmp_path / "fundamentals")
    assert f.cik_for("AAPL", date(2020, 1, 2)) == CIK
    assert f.for_ticker("AAPL", date(2020, 5, 2), ["revenues"]).shape[0] == 4
    again = mod.fetch(tmp_path, "Test Person test@example.com", refresh=False, max_age_days=7, limit=None)
    assert again["cached_not_downloaded"] == 1 and again["new_versions"] == 0 and len(calls) == 3  # 404 re-asked
    assert mod.build(tmp_path)["rows_parsed"] == 12
    assert mod.status(tmp_path)["rows"] == 12


def test_fetch_stops_on_403(tmp_path, monkeypatch):
    mod = _script()
    _fake_http(monkeypatch, status=403)
    _earnings_map(tmp_path)
    with pytest.raises(SystemExit, match="403"):
        mod.fetch(tmp_path, "Test Person test@example.com", refresh=False, max_age_days=7, limit=None)


def test_schema_is_stable():
    assert sf.SCHEMA.field("available_date").type == pa.date32()
    assert set(sf.METRICS) == {"revenues", "net_income", "operating_cash_flow", "gross_profit", "total_assets",
                               "total_liabilities", "long_term_debt", "shares_outstanding"}
