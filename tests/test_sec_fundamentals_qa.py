"""QA-Ops adversarial tests for the SEC fundamentals point-in-time store (PR #622). Hand-made companyfacts
documents only, no network. Each test tries to make a value visible before the day after its `filed` date, or
to make a later filing reach back in time."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from kaibot.data.fundamentals import Fundamentals
from kaibot.ingest import sec_fundamentals as sf
from kaibot.ingest.earnings_sec import Mapping_
from kaibot.ingest.sp500_pit import Alias, Change, Membership

CIK = "0000000042"


def _fact(start, end, val, accn, form, filed, fp="Q1", fy=2021):
    f = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start is not None:
        f["start"] = start
    return f


def _doc(revenues, assets=(), contract_revenues=()):
    gaap = {"Revenues": {"units": {"USD": list(revenues)}}}
    if contract_revenues:
        gaap["RevenueFromContractWithCustomerExcludingAssessedTax"] = {"units": {"USD": list(contract_revenues)}}
    if assets:
        gaap["Assets"] = {"units": {"USD": list(assets)}}
    return {"cik": int(CIK), "entityName": "QA CORP", "facts": {"us-gaap": gaap}}


def _store(tmp_path, doc):
    t, rep = sf.parse_companyfacts(doc, CIK)
    assert rep.bad == 0
    sf.merge_into_store(tmp_path, CIK, t)
    return Fundamentals(tmp_path)


def _rev(store, d, start, end):
    a = store.as_of(d, metrics=["revenues"])
    r = a[(a.period_start == start) & (a.period_end == end)]
    assert len(r) <= 1, "as_of must return one row per period"
    return None if r.empty else r.iloc[0].value


def _days(a: date, b: date):
    while a <= b:
        yield a
        a += timedelta(days=1)


# ---------------------------------------------------------------- the same period filed twice on one day


def test_same_period_filed_twice_on_the_same_day_is_hidden_that_day_and_deterministic_after(tmp_path):
    """A 10-Q and a same-day 10-Q/A (a corrected exhibit) carry two values for Q1. Neither may be seen on the
    filing day; from the next day the answer must be one row, the larger accession, whatever the input order."""
    q1 = ("2021-01-01", "2021-03-31")
    rows = [_fact(*q1, 100, "0000000042-21-000010", "10-Q", "2021-05-03"),
            _fact(*q1, 97, "0000000042-21-000011", "10-Q/A", "2021-05-03")]
    s, e = date(2021, 1, 1), date(2021, 3, 31)
    answers = set()
    for seed in range(6):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        store = _store(tmp_path / str(seed), _doc(shuffled))
        assert len(store.history(CIK, "revenues")) == 2  # both versions are kept
        assert _rev(store, date(2021, 5, 3), s, e) is None
        assert _rev(store, datetime(2021, 5, 3, 23, 59, 59), s, e) is None  # late on the filing day: still hidden
        answers.add(_rev(store, date(2021, 5, 4), s, e))
        assert store.latest(date(2021, 5, 3), "revenues", "Q").empty
    assert answers == {97.0}


def test_same_accession_two_values_same_day_is_stable(tmp_path):
    """One filing tagging the same period twice with different values (it happens with dimension slips): both
    are stored, nothing shows on the filing day, and the pick afterwards does not depend on row order."""
    q1 = ("2021-01-01", "2021-03-31")
    rows = [_fact(*q1, 100, "0000000042-21-000010", "10-Q", "2021-05-03"),
            _fact(*q1, 101, "0000000042-21-000010", "10-Q", "2021-05-03")]
    s, e = date(2021, 1, 1), date(2021, 3, 31)
    a = _rev(_store(tmp_path / "a", _doc(rows)), date(2021, 5, 4), s, e)
    b = _rev(_store(tmp_path / "b", _doc(rows[::-1])), date(2021, 5, 4), s, e)
    assert a == b and a in (100.0, 101.0)
    assert _rev(_store(tmp_path / "c", _doc(rows)), date(2021, 5, 3), s, e) is None


# ---------------------------------------------------------------- an amendment before the next quarter


def test_amendment_filed_before_the_next_quarter_never_reaches_back(tmp_path):
    """Q1 10-Q on 3 May, a 10-Q/A restating Q1 on 20 May (before the Q2 10-Q on 2 Aug, whose comparative column
    repeats the restated value). Day by day: nothing before 4 May, 100 from 4 to 20 May, 90 from 21 May on."""
    q1 = ("2021-01-01", "2021-03-31")
    q2 = ("2021-04-01", "2021-06-30")
    rows = [_fact(*q1, 100, "0000000042-21-000010", "10-Q", "2021-05-03"),
            _fact(*q1, 90, "0000000042-21-000012", "10-Q/A", "2021-05-20"),
            _fact(*q1, 90, "0000000042-21-000020", "10-Q", "2021-08-02", fp="Q2"),
            _fact(*q2, 120, "0000000042-21-000020", "10-Q", "2021-08-02", fp="Q2")]
    store = _store(tmp_path, _doc(rows))
    s, e = date(2021, 1, 1), date(2021, 3, 31)
    for d in _days(date(2021, 4, 1), date(2021, 9, 30)):
        got = _rev(store, d, s, e)
        if d <= date(2021, 5, 3):
            assert got is None, d
        elif d <= date(2021, 5, 20):
            assert got == 100, d
        else:
            assert got == 90, d
        lq = store.latest(d, "revenues", "Q")
        if d <= date(2021, 5, 3):
            assert lq.empty, d
        else:
            want = 120 if d > date(2021, 8, 2) else (100 if d <= date(2021, 5, 20) else 90)
            assert lq.iloc[0].value == want, d
    # the store keeps every version, and the amendment changed nothing about the past
    assert sorted(store.history(CIK, "revenues").value) == [90, 90, 100, 120]
    assert _rev(store, date(2021, 5, 20), s, e) == 100


def test_restatement_under_a_fallback_concept_does_not_leak_into_the_past(tmp_path):
    """The 10-K/A restates FY revenue under a lower-priority concept. Whatever the reader picks afterwards, it may
    never show the amended value before the day after the 10-K/A was filed."""
    fy = ("2020-01-01", "2020-12-31")
    rows = [_fact(*fy, 400, "0000000042-21-000005", "10-K", "2021-02-25", fp="FY", fy=2020)]
    alt = [_fact(*fy, 380, "0000000042-21-000030", "10-K/A", "2021-06-15", fp="FY", fy=2020)]
    store = _store(tmp_path, _doc(rows, contract_revenues=alt))
    s, e = date(2020, 1, 1), date(2020, 12, 31)
    for d in _days(date(2021, 2, 1), date(2021, 6, 15)):
        assert _rev(store, d, s, e) in (None, 400.0), d
    assert _rev(store, date(2021, 2, 25), s, e) is None


# ---------------------------------------------------------------- weekends and holidays


@pytest.mark.parametrize("filed", [
    date(2021, 7, 3),   # a Saturday (EDGAR is closed; such a date must still not be seen early)
    date(2021, 7, 4),   # a Sunday and Independence Day
    date(2021, 7, 5),   # the observed holiday (Monday)
    date(2021, 12, 24),  # a Friday before Christmas
    date(2021, 12, 31),  # year end, a Friday
])
def test_filing_on_a_weekend_or_holiday_is_hidden_on_that_calendar_day(tmp_path, filed):
    q = ("2021-01-01", "2021-03-31")
    store = _store(tmp_path, _doc([_fact(*q, 100, "0000000042-21-000010", "10-Q", filed.isoformat())]))
    s, e = date(2021, 1, 1), date(2021, 3, 31)
    for d in (filed - timedelta(days=1), filed, datetime.combine(filed, datetime.max.time()),
              pd.Timestamp(filed), filed.isoformat()):
        assert _rev(store, d, s, e) is None, d
    for d in (filed + timedelta(days=1), filed + timedelta(days=3)):
        assert _rev(store, d, s, e) == 100, d
    assert (store.facts.available_date == store.facts.filed + timedelta(days=1)).all()


def test_balance_sheet_instant_filed_friday_is_seen_from_saturday_only(tmp_path):
    a = [_fact(None, "2021-03-31", 1000, "0000000042-21-000010", "10-Q", "2021-04-30")]  # a Friday
    store = _store(tmp_path, _doc([], assets=a))
    assert store.latest(date(2021, 4, 30), "total_assets", "instant").empty
    assert store.latest(date(2021, 5, 1), "total_assets", "instant").iloc[0].value == 1000


# ---------------------------------------------------------------- ticker <-> CIK across a rename


def test_facts_follow_the_cik_across_the_fb_meta_rename(tmp_path):
    changes = [Change(date(2016, 1, 4), ("FB",)), Change(date(2022, 6, 9), ("META",), ("FB",))]
    m = Membership(changes, {"FB": [Alias("FB", date(2022, 6, 8), "META")]}, (), date(2026, 11, 24))
    sf.write_ciks(tmp_path, sf.ticker_spells(m, [Mapping_("META", CIK, "ticker", True)]))
    q = ("2022-01-01", "2022-03-31")
    store = _store(tmp_path, _doc([_fact(*q, 100, "0000000042-22-000010", "10-Q", "2022-04-28", fy=2022)]))
    assert store.cik_for("FB", date(2022, 6, 8)) == CIK == store.cik_for("META", date(2022, 6, 9))
    assert store.cik_for("META", date(2022, 6, 8)) is None and store.cik_for("FB", date(2022, 6, 9)) is None
    assert store.for_ticker("FB", date(2022, 6, 8), ["revenues"]).value.tolist() == [100]
    assert store.for_ticker("META", date(2022, 6, 9), ["revenues"]).value.tolist() == [100]
    assert store.for_ticker("META", date(2022, 6, 8), ["revenues"]).empty
