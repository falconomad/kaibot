"""Point-in-time S&P 500 membership (story S83, #123): change log, companies, keys, mask, holes, cross-check."""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.ingest import sp500_pit as pit

D = date
TODAY = D(2026, 11, 12)


def _m(changes, aliases=(), universe=(), today=TODAY) -> pit.Membership:
    amap: dict[str, list[pit.Alias]] = {}
    for a in aliases:
        amap.setdefault(a.ticker, []).append(a)
    return pit.Membership(list(changes), amap, list(universe), today)


# ---------------------------------------------------------------- change log


def test_change_log_takes_the_list_in_force_on_the_start_date():
    snaps = [(D(2015, 12, 1), frozenset({"A", "B"})), (D(2016, 1, 5), frozenset({"A", "C"})),
             (D(2016, 2, 1), frozenset({"A", "C"})), (D(2016, 3, 1), frozenset({"C", "D"}))]
    log = pit.change_log(snaps)
    assert log[0] == pit.Change(D(2016, 1, 1), ("A", "B"))
    assert [(c.date, c.added, c.removed) for c in log[1:]] == [
        (D(2016, 1, 5), ("C",), ("B",)), (D(2016, 3, 1), ("D",), ("A",))]


def test_supplement_is_appended_only_after_the_log_and_must_fit():
    log = [pit.Change(D(2016, 1, 1), ("A", "B"))]
    merged, skipped = pit.merge_supplement(log, [pit.Change(D(2015, 6, 1), ("Z",)),
                                                 pit.Change(D(2016, 6, 1), ("C",), ("A",), "x")])
    assert [c.date for c in merged] == [D(2016, 1, 1), D(2016, 6, 1)] and len(skipped) == 1
    with pytest.raises(ValueError, match="do not fit"):
        pit.merge_supplement(log, [pit.Change(D(2016, 6, 1), ("C",), ("Q",))])


def test_change_log_round_trip(tmp_path):
    log = [pit.Change(D(2016, 1, 1), ("A", "BRK.B")), pit.Change(D(2017, 1, 3), ("C",), ("A",), "https://x/y,z")]
    pit.write_changes(log, tmp_path / "c.csv")
    assert pit.read_changes(tmp_path / "c.csv") == log


# ---------------------------------------------------------------- companies and spells


BASE = [pit.Change(D(2016, 1, 1), ("FB", "DOW", "GOOG", "GOOGL", "OLD", "KORS", "CPRI", "XYZ")),
        pit.Change(D(2017, 9, 1), ("DWDP",), ("DOW",)),
        pit.Change(D(2018, 9, 19), (), ("KORS",)),
        pit.Change(D(2019, 4, 2), ("DOW",), ("OLD",)),
        pit.Change(D(2020, 5, 12), (), ("CPRI",)),
        pit.Change(D(2022, 6, 9), ("META",), ("FB",))]
ALIASES = [pit.Alias("FB", D(2022, 6, 8), "META"), pit.Alias("DOW", D(2017, 8, 31), "DOW#2017", "reuse"),
           pit.Alias("KORS", D(2018, 12, 31), "CPRI"), pit.Alias("CPRI", D(2018, 12, 31), "CPRI", "dup")]


def test_renames_reuse_duplicates_and_share_classes():
    m = _m(BASE, ALIASES, universe=["META", "DOW", "GOOGL"])
    assert m.members_on(D(2016, 6, 1)) == {"FB", "DOW", "GOOG", "GOOGL", "OLD", "KORS", "XYZ"}  # CPRI: duplicate
    assert m.members_on(D(2018, 10, 1)) >= {"CPRI"}  # KORS gone from the source: CPRI stands for the company
    assert m.companies_on(D(2016, 6, 1)) == {"META", "DOW#2017", "GOOGL", "OLD", "CPRI", "XYZ"}
    assert [(s.start, s.end, s.tickers) for s in m.spells["META"]] == [(D(2016, 1, 1), None, ("FB", "META"))]
    assert [(s.start, s.end) for s in m.spells["DOW#2017"]] == [(D(2016, 1, 1), D(2017, 9, 1))]
    assert [(s.start, s.end) for s in m.spells["DOW"]] == [(D(2019, 4, 2), None)]
    assert [(s.start, s.end, s.tickers) for s in m.spells["CPRI"]] == [(D(2016, 1, 1), D(2020, 5, 12),
                                                                         ("KORS", "CPRI"))]
    assert "GOOG" not in m.spells


def test_keys_and_former_members():
    m = _m(BASE, ALIASES, universe=["META", "DOW", "GOOGL", "XYZ"])
    assert m.keys("META") == ["META", "META@2026-11-05"]
    assert m.keys("DOW#2017") == ["DOW@2017-08-24"]  # not the 288's DOW: a different company
    assert m.request("CPRI") == ("CPRI", D(2020, 5, 4))
    rows = m.former_members()
    assert ("DOW", D(2017, 8, 24)) in rows and ("OLD", D(2019, 3, 25)) in rows and ("CPRI", D(2020, 5, 4)) in rows
    assert all(s not in {"META", "GOOGL", "XYZ"} for s, _ in rows)  # downloaded with today's universe already
    assert ("DWDP", D(2026, 11, 5)) in rows  # a current member outside the 288 (in this toy universe)
    for c in m.spells:
        if m.needs_request(c):
            sym, asof = m.request(c)
            assert any(sp.covers(asof) for sp in m.spells[c]), c


def test_mask_marks_member_days_of_the_chosen_column():
    m = _m(BASE, ALIASES, universe=["META", "DOW"])
    dates = np.array(["2017-08-31", "2017-09-01", "2019-04-01", "2019-04-02"], dtype="datetime64[D]")
    cols = ["META", "DOW", "DOW@2017-08-24", "SPY", "OLD@2019-03-25"]
    mk = m.mask(dates, cols)
    assert mk[:, 0].all() and not mk[:, 3].any()
    assert mk[:, 1].tolist() == [False, False, False, True]  # Dow Inc. from 2019-04-02 only
    assert mk[:, 2].tolist() == [True, False, False, False]  # Dow Chemical until 2017-08-31
    assert mk[:, 4].tolist() == [True, True, True, False]
    with pytest.raises(ValueError):
        m.mask(np.array(["2015-12-31"], dtype="datetime64[D]"), cols)


def test_mask_falls_back_to_the_asof_key():
    m = _m(BASE, ALIASES, universe=["META"])
    mk = m.mask([D(2018, 1, 2)], ["META@2026-11-05"])
    assert mk.tolist() == [[True]]


# ---------------------------------------------------------------- the committed data


@pytest.fixture(scope="module")
def real() -> pit.Membership:
    return pit.Membership.load(today=D(2026, 11, 12))


def test_committed_log_counts_and_known_facts(real):
    assert real.first == D(2016, 1, 1)
    d = real.first
    while d <= D(2026, 11, 12):  # about 500 companies on every first of the month
        assert 495 <= len(real.companies_on(d)) <= 505, d
        d = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    assert len(real.current()) == 500
    assert "SIVB" in real.companies_on(D(2022, 6, 30)) and "SIVB" not in real.companies_on(D(2023, 6, 30))
    assert "FB" in real.members_on(D(2018, 1, 2)) and "META" in real.companies_on(D(2018, 1, 2))
    assert "TSLA" not in real.companies_on(D(2020, 12, 18)) and "TSLA" in real.companies_on(D(2020, 12, 21))
    assert "OKTA" in real.companies_on(D(2026, 11, 4)) and "KVUE" not in real.companies_on(D(2026, 11, 4))
    assert real.yearly()[0]["year"] == 2016


def test_committed_former_members_meet_the_fetcher_contract(real, tmp_path):
    n = pit.write_symbols_file(real.former_members(), tmp_path / "f.csv")
    rows = list(csv.reader((tmp_path / "f.csv").open()))
    assert rows[0] == ["symbol", "asof_date"] and len(rows) == n + 1 and n > 300
    sym = re.compile(r"[A-Z][A-Z0-9]{0,9}(\.[A-Z]{1,2})?")
    for s, d in rows[1:]:
        assert sym.fullmatch(s), s
        assert pit.START <= D.fromisoformat(d) <= D(2026, 11, 12)
    assert len({tuple(r) for r in rows[1:]}) == n


def test_ticker_map_and_outcomes_parse(real):
    kinds = {a.kind for v in real.aliases.values() for a in v}
    assert kinds <= {"rename", "reuse", "dup"}
    out = pit.read_outcomes()
    assert pit.terminal_return("SIVB", out) == -1.0
    assert pit.terminal_return("NOPE", out) == pit.DEFAULT_TERMINAL_RETURN


# ---------------------------------------------------------------- holes and cross-check


def _store(root, key, days):
    (root / "bars").mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"date": pa.array(days, pa.date32())}), root / "bars" / f"symbol={key}.parquet")


def test_hole_log(tmp_path):
    changes = [pit.Change(D(2016, 1, 1), ("AAA", "BBB", "CCC")), pit.Change(D(2018, 1, 2), (), ("BBB", "CCC"))]
    m = _m(changes, universe=["AAA"], today=D(2019, 1, 1))
    days = [D(2016, 1, 1) + timedelta(days=i) for i in range(0, 3 * 365)]
    _store(tmp_path, "AAA", days)  # bars stop 2018-12-30: fine (slack)... and 2018-12-31 too
    _store(tmp_path, "BBB@2017-12-25", [d for d in days if d <= D(2017, 6, 30)])  # stops half a year early
    rows = {r["company"]: r for r in pit.hole_log(m, tmp_path, {"BBB": (0.0, "taken over")})}
    assert "AAA" not in rows
    assert rows["BBB"]["kind"] == "ends_early" and rows["BBB"]["terminal_return"] == 0.0
    assert rows["CCC"]["kind"] == "no_bars" and rows["CCC"]["key"] == "CCC@2017-12-25"
    pit.write_hole_log(list(rows.values()), tmp_path / "h.csv")
    assert (tmp_path / "h.csv").read_text().startswith("company,key,")


def test_crosscheck_against_an_nport_file(tmp_path, real):
    d = D(2026, 6, 30)
    names = {r["ticker"]: r["ivv_name"] for r in pit._rows(pit.IVV_NAMES)}
    lines = [names[t] for t in sorted(real.members_on(d))]
    body = "".join(f"<invstOrSec><name>{n.replace('&', '&amp;')}</name><assetCat>EC</assetCat></invstOrSec>"
                   for n in lines[1:]) + "<invstOrSec><name>Cash</name><assetCat>STIV</assetCat></invstOrSec>"
    x = tmp_path / "p.xml"
    x.write_text(f'<edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><repPdDate>{d}</repPdDate>'
                 f"{body}<invstOrSec><name>Not A Member Inc</name><assetCat>EC</assetCat></invstOrSec>"
                 "</edgarSubmission>")
    dd, got = pit.read_nport(x)
    only_list, only_fund = pit.crosscheck(real, dd, got)
    assert dd == d and only_list == [sorted(real.members_on(d))[0]] and only_fund == ["Not A Member Inc"]
