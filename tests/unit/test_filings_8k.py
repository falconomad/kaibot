"""Every 8-K and 8-K/A with its items (story S100, #374): the all-items extract of earnings_sec, the table, the
point-in-time reader, the coverage report, the script and the isolation rule.

Offline. Every SEC response comes from tests/fixtures/earnings: HAND-BUILT files in the shape of EDGAR's submissions
JSON for three fictional companies (ACME, BETA, OLDCO), the same ones the S85 tests use. No real filing is in them.
Blocks built inline in this file are hand-built too."""

from __future__ import annotations

import ast
import json
import random
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
import yaml

from kaibot.ingest import earnings_sec as sec
from kaibot.ingest import filings_pit as fp
from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest import sp500_pit as pit

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "earnings"
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "kaibot"
D = date
UA = "Kaibot research ops@example.com"


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _doc(name: str):
    return json.loads((FIX / name).read_text())


class FakeSec:
    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        p = FIX / url.rsplit("/", 1)[-1]
        if not p.is_file():
            raise RuntimeError(f"HTTP 404 for {url}")
        return p.read_bytes()


def _membership() -> pit.Membership:
    changes = [pit.Change(D(2016, 1, 1), ("GONE", "OLDC", "XYZ")),
               pit.Change(D(2019, 6, 3), ("BETA",), ("GONE", "OLDC")),
               pit.Change(D(2020, 1, 2), ("ACME",), ("XYZ",))]
    aliases = {"XYZ": [pit.Alias("XYZ", D(2020, 1, 1), "ACME")]}
    return pit.Membership(changes, aliases, [], D(2026, 11, 12))


NAMES = {"ACME": {"XYZ HOLDINGS"}, "OLDC": {"OLDCO INDUSTRIES"}, "BETA": set(), "GONE": set()}


def _mapping(cache: sec.Cache) -> list[sec.Mapping_]:
    tickers = sec.ticker_ciks(_doc("company_tickers.json"))
    lookup = lambda: sec.parse_cik_lookup((FIX / "cik-lookup-data.txt").read_text())  # noqa: E731
    return sec.resolve(_membership(), tickers, {}, cache.company, NAMES, lookup)


def _tables(tmp_path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(every 8-K, the S85 announcements) from the same fake SEC, each through its own cache."""
    earn = sec.Cache(tmp_path / "earn", FakeSec())
    mapping = _mapping(earn)
    alli = sec.Cache(tmp_path / "all", FakeSec(), forms=sec.ALL_FORMS, item=None)
    for mp in mapping:
        if mp.cik:
            alli.company(mp.cik)
    return sec.build_filings(_membership(), mapping, alli.load), sec.build(_membership(), mapping, earn.load)


# ---------------------------------------------------------------- the extract


def test_all_items_parse_keeps_every_8k_and_amendment_since_2016():
    block = _doc("CIK0000000101-submissions-001.json")
    filings, _ = sec.parse_block(block, forms=sec.ALL_FORMS, item=None)
    assert [(f.accession[-2:], f.form, f.items) for f in filings] == [
        ("04", "8-K", "5.07"), ("03", "8-K/A", "2.02"), ("02", "8-K", "2.02,9.01"), ("01", "8-K", "2.02,9.01")]
    default, _ = sec.parse_block(block)  # the S85 extract is unchanged: 8-K with Item 2.02 only
    assert [f.accession[-2:] for f in default] == ["02", "01"]


def test_all_items_parse_keeps_items_as_filed_and_skips_malformed_rows():
    block = {"accessionNumber": ["0000000777-20-000001", "0000000777-20-000002", "bad", "0000000777-20-000004",
                                 "0000000777-20-000005"],
             "filingDate": ["2020-03-02", "2020-03-03", "2020-03-04", "2020-03-05", "2020-03-06"],
             "acceptanceDateTime": ["2020-03-02T21:10:00.000Z", "2020-03-03T13:00:00.000Z",
                                    "2020-03-04T13:00:00.000Z", "nope", "2020-03-06T13:00:00.000Z"],
             "form": ["8-K", "8-K", "8-K", "8-K", "10-Q"],
             "items": ["5.02, 9.01", "", "1.01", "4.02", ""]}
    filings, periodic = sec.parse_block(block, forms=sec.ALL_FORMS, item=None)
    assert [(f.accession[-1], f.items) for f in filings] == [("1", "5.02, 9.01"), ("2", "")]  # as filed, even empty
    assert periodic == [D(2020, 3, 6)]


def test_all_items_cache_is_its_own_resume_state(tmp_path):
    get = FakeSec()
    c = sec.Cache(tmp_path / "all", get, forms=sec.ALL_FORMS, item=None)
    co = c.company("0000000101")
    assert len(get.urls) == 2 and [f.form for f in co.filings].count("8-K/A") == 1 and len(co.filings) == 8
    assert sec.Cache(tmp_path / "all", get, forms=sec.ALL_FORMS, item=None).company("0000000101").filings == co.filings
    assert len(get.urls) == 2  # a finished company is not fetched again
    sec.Cache(tmp_path / "all", get, forms=sec.ALL_FORMS, item=None).company("0000000101", refresh=True)
    assert len(get.urls) == 3 and get.urls[-1].endswith("CIK0000000101.json")  # a refresh reads `recent` only
    assert len(sec.Cache(tmp_path / "all", None, forms=sec.ALL_FORMS, item=None).load("0000000101").filings) == 8


# ---------------------------------------------------------------- the table


def test_build_filings_columns_sessions_and_items(tmp_path):
    df, _ = _tables(tmp_path)
    assert list(df.columns) == sec.FILINGS_COLUMNS
    assert df.groupby("company").size().to_dict() == {"ACME": 8, "BETA": 2, "OLDC": 3}
    acme = df[df["company"] == "ACME"].reset_index(drop=True)
    assert list(acme["symbol"]) == ["XYZ"] * 8  # the index ticker in force that day (ACME only from 2020)
    vote = acme[acme["items"] == "5.07"].iloc[0]  # accepted 2016-05-10 16:00 ET: at the close
    assert (vote["form"], vote["session"], vote["last_close_before"]) == ("8-K", D(2016, 5, 11), D(2016, 5, 10))
    amend = acme[acme["form"] == "8-K/A"].iloc[0]  # 2016-04-27 09:00 ET, before the open
    assert (amend["items"], amend["session"], amend["last_close_before"]) == ("2.02", D(2016, 4, 27), D(2016, 4, 26))
    assert str(df["accepted_ts"].dt.tz) == "UTC" and df["accession"].is_unique
    assert (df["session"].map(nyse.open_utc) > df["accepted_ts"].dt.to_pydatetime()).all()
    assert df.equals(df.sort_values(["company", "accepted_ts", "accession"], ignore_index=True))
    assert sec.tz_check(df)["utc_reading_ok"]  # the acceptance-time check of S85, on the new table


def test_item_202_rows_equal_the_announcements_table(tmp_path):
    df, ann = _tables(tmp_path)
    cmp_ = fp.compare_item_202(df, ann)
    assert cmp_["equal"] and cmp_["equal_up_to_cutoff"] and cmp_["value_mismatches"] == 0
    assert cmp_["announcements_rows"] == cmp_["item_202_rows"] == 11  # the 8-K/A with 2.02 is not an announcement
    later = pd.concat([df, df.iloc[[0]].assign(accession="0000000101-27-000001", items="2.02", form="8-K",
                                               accepted_ts=utc("2027-01-26T21:05:00"))], ignore_index=True)
    c2 = fp.compare_item_202(later, ann)  # a filing after the announcements were built is new, not a difference
    assert not c2["equal"] and c2["equal_up_to_cutoff"] and c2["n_only_in_filings"] == 1
    c3 = fp.compare_item_202(df[df["accession"] != ann["accession"].iloc[0]], ann)
    assert not c3["equal_up_to_cutoff"] and c3["only_in_announcements"] == [ann["accession"].iloc[0]]


def test_has_item_matches_whole_items_only():
    s = pd.Series(["2.02,9.01", "5.02, 9.01", "12.02", "", "2.02"])
    assert fp.has_item(s, "2.02").tolist() == [True, False, False, False, True]
    assert fp.has_item(s, "9.01").tolist() == [True, True, False, False, False]


# ---------------------------------------------------------------- the point-in-time reader


def _reader(tmp_path) -> fp.Filings:
    return fp.Filings(_tables(tmp_path)[0], _membership())


def test_reader_never_returns_a_filing_before_it_was_accepted(tmp_path):
    f = _reader(tmp_path)
    t = utc("2016-01-26T21:05:00")
    assert len(f.filings("ACME", t - timedelta(seconds=1))) == 0
    assert list(f.filings("ACME", t)["accession"]) == ["0000000101-16-000001"]
    assert list(f.filings("XYZ", utc("2016-05-10T19:59:59"))["items"]) == ["2.02,9.01", "2.02,9.01", "2.02"]
    at_close = f.filings("XYZ@2019-12-20", utc("2016-05-10T20:00:00"))
    assert list(at_close["items"])[-1] == "5.07"
    assert list(f.filings("ACME", utc("2026-01-01T00:00:00"), items=["5.07"])["accession"]) == ["0000000101-16-000004"]
    assert set(f.filings("ACME", utc("2026-01-01T00:00:00"), forms=["8-K/A"])["form"]) == {"8-K/A"}
    assert len(f.filings("NOPE", t)) == 0 and list(f.filings("NOPE", t).columns) == sec.FILINGS_COLUMNS
    with pytest.raises(ValueError):
        f.filings("ACME", datetime(2016, 3, 1))  # a decision time has a time zone


def test_point_in_time_invariance(tmp_path):
    """The same invariance as S85's test_point_in_time_invariance: for any decision time the answer is the same
    when every filing accepted later is deleted, and nothing in it is newer than the decision time."""
    table = _tables(tmp_path)[0]
    full = fp.Filings(table, _membership())
    rng = random.Random(11)
    lo, hi = utc("2015-12-01T00:00:00").timestamp(), utc("2026-12-31T00:00:00").timestamp()
    times = [datetime.fromtimestamp(rng.uniform(lo, hi), UTC) for _ in range(150)]
    times += [ts + timedelta(seconds=s) for ts in table["accepted_ts"].dt.to_pydatetime() for s in (-1, 0, 1)]
    for t in times:
        cut = fp.Filings(table[table["accepted_ts"] <= t], _membership())
        for s in ("ACME", "OLDC", "BETA", "XYZ"):
            for items in (None, ["2.02"], ["5.07", "9.01"]):
                a, b = full.filings(s, t, items=items), cut.filings(s, t, items=items)
                assert a.reset_index(drop=True).equals(b.reset_index(drop=True)), (s, t, items)
                assert (a["accepted_ts"] <= t).all()


# ---------------------------------------------------------------- the coverage report


def _row(ts: datetime, items: str, form: str = "8-K", company: str = "AAA", n: int = 0) -> dict:
    return {"symbol": company, "company": company, "cik": "0000000001", "accepted_ts": ts.astimezone(UTC),
            "session": nyse.first_session_opening_after(ts), "last_close_before": nyse.last_session_closed_by(ts),
            "filing_date": ts.astimezone(nyse.ET).date(), "form": form, "items": items,
            "accession": f"0000000001-24-{n:06d}"}


def test_coverage_counts_per_year_per_item_and_outside_hours():
    et = lambda s: datetime.fromisoformat(s).replace(tzinfo=nyse.ET)  # noqa: E731
    rows = [_row(et("2023-10-26T07:00:00"), "2.02,9.01", n=1),  # before the open
            _row(et("2024-10-29T11:00:00"), "5.02", n=2),  # inside regular hours
            _row(et("2024-10-29T16:05:00"), "2.02,9.01", n=3),  # after the close
            _row(et("2024-11-29T13:30:00"), "8.01", n=4),  # after an early close (13:00)
            _row(et("2024-11-30T10:00:00"), "", form="8-K/A", n=5)]  # a Saturday, no items
    df = pd.DataFrame(rows, columns=sec.FILINGS_COLUMNS)
    cov = fp.coverage(df, mapped=["AAA", "BBB", "CCC"])
    assert cov["rows"] == 5 and cov["companies_with_rows"] == 1 and cov["companies_with_none"] == ["BBB", "CCC"]
    assert cov["per_year"] == {2023: 1, 2024: 4} and cov["per_form"] == {"8-K": 4, "8-K/A": 1}
    assert cov["per_item"] == {"(none)": 1, "2.02": 2, "5.02": 1, "8.01": 1, "9.01": 2}
    assert cov["per_item_year"]["2.02"] == {2023: 1, 2024: 1} and cov["per_item_year"]["(none)"] == {2024: 1}
    assert cov["outside_regular_hours_share"] == 0.8 and cov["outside_regular_hours_rows"] == 4
    md = fp.markdown(cov)
    assert "| 2.02 | 1 | 1 | 2 |" in md and "| item | 2023 | 2024 | all |" in md
    assert fp.coverage(df.iloc[0:0], mapped=["AAA"])["rows"] == 0


# ---------------------------------------------------------------- the script, end to end with a fake SEC


def _script(monkeypatch, get):
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_earnings_dates as script

    monkeypatch.setattr(sec, "sec_getter", lambda ua: get)
    monkeypatch.setattr(script.Membership, "load", classmethod(lambda cls, *a, **k: _membership()))
    monkeypatch.setattr(sec, "company_names", lambda m: NAMES)
    return script


def _subs(urls: list[str]) -> int:
    return sum("/submissions/" in u for u in urls)


def test_all_items_run_is_resumable_and_leaves_announcements_byte_identical(tmp_path, monkeypatch):
    get = FakeSec()
    script = _script(monkeypatch, get)
    earn, out = tmp_path / "earnings", tmp_path / "filings"
    (earn / "sec").mkdir(parents=True)
    (earn / "sec" / "cik-lookup-data.txt").write_text((FIX / "cik-lookup-data.txt").read_text())
    script.fetch_and_build(earn, UA, refresh=False)
    before = (earn / "announcements.parquet").read_bytes()

    n0 = len(get.urls)
    meta = script.fetch_all_items(earn, out, UA, refresh=False)
    assert _subs(get.urls[n0:]) == 4 and len(get.urls) - n0 == 4  # 3 companies + 1 older file; the map is reused
    assert meta["rows"] == 13 and meta["companies_with_rows"] == 3 and meta["errors"] == {}
    assert meta["mapping_from"].endswith("cik_map.csv") and meta["tz_check"]["utc_reading_ok"]
    assert meta["item_202_vs_announcements"]["equal"] and meta["requests_this_run"] == 4
    assert (earn / "announcements.parquet").read_bytes() == before  # not touched by the all-items run
    for f in ("filings_8k.parquet", "build.json", "coverage.json", "coverage.md", "cik_map.csv"):
        assert (out / f).is_file(), f
    df = pd.read_parquet(out / "filings_8k.parquet")
    assert list(df.columns) == sec.FILINGS_COLUMNS and len(df) == 13

    n1 = len(get.urls)
    again = script.fetch_all_items(earn, out, UA, refresh=False)
    assert len(get.urls) == n1 and again["rows"] == 13 and again["requests_this_run"] == 0  # resumed: nothing to do
    script.fetch_all_items(earn, out, UA, refresh=True)
    assert [u.rsplit("/", 1)[-1] for u in get.urls[n1:]] == [
        "CIK0000000101.json", "CIK0000000202.json", "CIK0000000303.json"]  # newest filings only
    assert script.offline_all_items(earn, out)["rows"] == 13
    assert pd.read_parquet(out / "filings_8k.parquet").equals(df)

    script.offline_build(earn)  # the S85 table rebuilt after the all-items run: the same bytes
    assert (earn / "announcements.parquet").read_bytes() == before


def test_all_items_alone_resolves_the_mapping_itself(tmp_path, monkeypatch):
    get = FakeSec()
    script = _script(monkeypatch, get)
    earn, out = tmp_path / "earnings", tmp_path / "filings"
    (out / "sec").mkdir(parents=True)
    (out / "sec" / "cik-lookup-data.txt").write_text((FIX / "cik-lookup-data.txt").read_text())
    meta = script.fetch_all_items(earn, out, UA, refresh=False)
    assert meta["rows"] == 13 and meta["mapping_from"] == "resolved by this run"
    assert meta["item_202_vs_announcements"] == {"skipped": "no announcements.parquet on this machine"}
    assert not earn.exists()  # the earnings store is never written by --all-items


def test_a_failed_company_is_reported_and_retried_next_run(tmp_path, monkeypatch):
    class Flaky(FakeSec):
        fail = True

        def __call__(self, url):
            if self.fail and url.endswith("CIK0000000303.json"):
                self.urls.append(url)
                raise RuntimeError("HTTP 503")
            return super().__call__(url)

    get = Flaky()
    script = _script(monkeypatch, get)
    earn, out = tmp_path / "earnings", tmp_path / "filings"
    (earn / "sec").mkdir(parents=True)
    (earn / "sec" / "cik-lookup-data.txt").write_text((FIX / "cik-lookup-data.txt").read_text())
    Flaky.fail = False
    script.fetch_and_build(earn, UA, refresh=False)
    Flaky.fail = True
    meta = script.fetch_all_items(earn, out, UA, refresh=False)
    assert list(meta["errors"]) == ["0000000303"] and meta["rows"] == 11 and meta["companies_with_none"] == ["BETA"]
    Flaky.fail = False
    n = len(get.urls)
    meta = script.fetch_all_items(earn, out, UA, refresh=False)
    assert [u.rsplit("/", 1)[-1] for u in get.urls[n:]] == ["CIK0000000303.json"] and meta["rows"] == 13


def test_cli_all_items_needs_the_declared_user_agent(tmp_path, monkeypatch, capsys):
    script = _script(monkeypatch, FakeSec())
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / "empty.env"
    env.write_text("# nothing\n")
    monkeypatch.setattr(sys, "argv", ["fetch_earnings_dates.py", "--env", str(env), "--all-items",
                                      "--data", str(tmp_path / "e"), "--filings-data", str(tmp_path / "f")])
    assert script.main() == 1 and "SEC_USER_AGENT" in capsys.readouterr().out
    assert not (tmp_path / "f" / "filings_8k.parquet").exists()


# ---------------------------------------------------------------- fair access, budget, wrapper, isolation


def test_every_all_items_request_runs_under_the_shared_sec_budget(monkeypatch):
    from kaibot.common import http

    seen = []

    def fake(url, **kw):
        seen.append((url, kw["source"], kw["headers"]["User-Agent"]))
        return http.Response(200, {}, (FIX / url.rsplit("/", 1)[-1]).read_bytes())

    monkeypatch.setattr(http, "get", fake)
    get = sec.sec_getter(UA)
    doc = json.loads(get(sec.SUBMISSIONS_URL.format(name="CIK0000000303.json")))
    assert sec.company_from_submissions("0000000303", doc, {}, forms=sec.ALL_FORMS, item=None).filings
    assert seen == [("https://data.sec.gov/submissions/CIK0000000303.json", "sec_edgar", UA)]


def test_sources_yaml_names_the_new_use_under_the_sec_budget():
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())["fundamentals_filings"]
    assert cfg["sec_edgar"]["budget_per_s"] == 5  # unchanged: half of SEC's fair-access limit of 10
    entry = cfg["sec_filings_8k"]
    assert "requests count against sec_edgar" in entry["note"] and "--all-items" in entry["note"]
    assert not any(k.startswith("budget_") for k in entry)  # no budget of its own


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_takes_all_items_and_refuses_unknown_options(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "fetch_earnings_dates.sh", tmp_path / "scripts" / "fetch_earnings_dates.sh")
    bin_ = tmp_path / ".venv" / "bin"
    bin_.mkdir(parents=True)
    stub = '#!/bin/sh\ncase "$*" in *"-m certifi"*) echo /dev/null;; *) echo "ARGS $*";; esac\n'
    for name, text in (("python", stub), ("pip", "#!/bin/sh\nexit 0\n")):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=placeholder\n")

    def run(*args):
        return subprocess.run(["bash", "scripts/fetch_earnings_dates.sh", *args], cwd=tmp_path, text=True,
                              capture_output=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                                                        "KAIBOT_ENV_FILE": str(env)})

    r = run("--all-items")
    assert r.returncode == 0 and f"--env {env} --all-items" in r.stdout
    assert f"--env {env} --all-items --refresh" in run("--all-items", "--refresh").stdout
    assert "ARGS scripts/fetch_earnings_dates.py --all-items --build" in run("--all-items", "--build").stdout
    assert "ARGS scripts/fetch_earnings_dates.py --all-items --status" in run("--all-items", "--status").stdout
    assert run("--all-items", "--nope").returncode == 2 and run("--every-item").returncode == 2
    assert "--env" in run().stdout and "--all-items" not in run().stdout  # the S85 run is unchanged


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_trading_code_never_imports_the_filings_store():
    """Research store only: nothing under engine, risk, strategies or broker imports the extract or its reader,
    or names the store's files."""
    banned = ("kaibot.ingest.filings_pit", "kaibot.ingest.earnings_sec")
    offenders = []
    for pkg in ("engine", "risk", "strategies", "broker"):
        for f in (SRC / pkg).rglob("*.py"):
            text = f.read_text()
            if (any(m.startswith(banned) for m in _imports(f)) or "filings_8k" in text or "data/filings" in text
                    or "filings_pit" in text):
                offenders.append(str(f.relative_to(SRC)))
    assert offenders == []
