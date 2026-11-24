"""Company fundamentals from SEC EDGAR XBRL companyfacts, every filed version kept (research store; not read by the
engine). Reader with `as_of(date)` semantics: `kaibot.data.fundamentals`. Docs: docs/data/fundamentals.md.

Source (checked 24 Nov 2026, sec.gov "EDGAR Application Programming Interfaces" and "Accessing EDGAR Data"):
- https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json: "all the company concepts data for a company" in
  one call: facts -> taxonomy -> concept -> units -> unit -> [{start?, end, val, accn, fy, fp, form, filed, frame?}].
  A value reported again in a later filing (a 10-K's comparative column, a 10-K/A, a restated 10-Q) is a separate
  row with that filing's `accn` and `filed`, so the file is the history of every filed version.
- No key; fair access: "Current max request rate: 10 requests/second" with a declared User-Agent. Every request
  goes through the shared `sec_edgar` budget (config/sources.yaml, 5 req/s) with KAIBOT_SEC_USER_AGENT.
- "Information presented on www.sec.gov is considered public information and may be copied or further
  distributed" (SEC web site privacy and security policy).

Point in time: a row is usable from `available_date = filed + 1 day` (companyfacts gives the filing date, not the
acceptance time, and EDGAR dates a filing accepted after 17:30 ET on the next business day, so the day after is the
first day it was surely public). The period end is never used for availability.

Not used, on purpose:
- the `frame` field and the frames API (/api/xbrl/frames/): SEC "aggregates one fact for each reporting entity
  that is last filed", i.e. the frame is assigned with hindsight to the latest filed version. Using it would leak
  restatements into the past.
- `fy` / `fp` are the fiscal year and period of the FILING, not of the value (a 10-K for FY2023 carries FY2021
  values with fy=2023). They are kept as provenance; periods are `period_start` / `period_end`.

All input is untrusted JSON: types, dates, numbers and accession numbers are checked; bad rows are counted and
skipped.
"""

from __future__ import annotations

import gzip
import logging
import math
import re
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from kaibot.ingest.earnings_sec import cik10

log = logging.getLogger(__name__)

BUDGET = "sec_edgar"
URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
MAX_BYTES = 128 * 1024 * 1024  # the largest companyfacts files (big banks) are tens of MB uncompressed
AVAILABLE_LAG = timedelta(days=1)
FIRST_FILED = date(2009, 1, 1)  # XBRL filing began in 2009 (large accelerated filers); nothing earlier exists

# metric -> [(taxonomy, concept)] in priority order; the reader uses the first concept a company reports for a
# period. Units: USD for money, shares for share counts (any other unit is skipped).
METRICS: dict[str, tuple[tuple[str, str], ...]] = {
    "revenues": (
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "SalesRevenueGoodsNet"),
        ("us-gaap", "SalesRevenueServicesNet"),
    ),
    "net_income": (
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
        ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),
    ),
    "operating_cash_flow": (
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    ),
    "gross_profit": (("us-gaap", "GrossProfit"),),
    "total_assets": (("us-gaap", "Assets"),),
    "total_liabilities": (("us-gaap", "Liabilities"),),
    "long_term_debt": (
        ("us-gaap", "LongTermDebt"),
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebtAndCapitalLeaseObligations"),
    ),
    "shares_outstanding": (
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
    ),
}
UNITS = {"shares_outstanding": "shares"}  # everything else: USD
CONCEPTS: dict[tuple[str, str], tuple[str, int]] = {
    tc: (m, i) for m, tcs in METRICS.items() for i, tc in enumerate(tcs)
}

SCHEMA = pa.schema([
    ("cik", pa.string()),
    ("metric", pa.string()),
    ("priority", pa.int8()),  # index of the concept in METRICS[metric]; 0 = preferred
    ("taxonomy", pa.string()),
    ("concept", pa.string()),
    ("unit", pa.string()),
    ("period_start", pa.date32()),  # null for an instant (balance sheet, share count)
    ("period_end", pa.date32()),
    ("duration_days", pa.int32()),  # null for an instant
    ("value", pa.float64()),
    ("accn", pa.string()),
    ("form", pa.string()),
    ("fy", pa.int32()),  # of the filing, not of the value
    ("fp", pa.string()),  # of the filing, not of the value
    ("filed", pa.date32()),
    ("available_date", pa.date32()),  # filed + 1 day: the first day the value may be used
    ("first_seen_ts", pa.timestamp("us", tz="UTC")),  # when Kaibot first downloaded this version
])
VERSION_KEY = ["cik", "taxonomy", "concept", "unit", "period_start", "period_end", "accn", "value"]
_ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_FORM = re.compile(r"^[A-Z0-9][A-Z0-9 ./-]{0,19}$")
_FP = re.compile(r"^(FY|Q[1-4]|H[12]|M\d{1,2})$")


@dataclass
class ParseReport:
    rows: int = 0
    bad: int = 0
    other_units: int = 0
    entity: str = ""


def _date(s: object) -> date | None:
    if not isinstance(s, str) or len(s) != 10:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def parse_companyfacts(doc: object, cik: str, seen_ts: datetime | None = None) -> tuple[pa.Table, ParseReport]:
    """The rows of METRICS from one companyfacts document (untrusted). One row per filed version."""
    cik = cik10(cik)
    seen_ts = seen_ts or datetime.now(UTC)
    rep = ParseReport()
    if not isinstance(doc, dict) or not isinstance(doc.get("facts"), dict):
        raise ValueError("companyfacts: unexpected schema (no facts object)")
    got = doc.get("cik")
    if got is not None and cik10(got) != cik:  # cik10 also raises on a malformed CIK
        raise ValueError(f"companyfacts: document is for CIK {str(got)[:12]}, asked for {cik}")
    rep.entity = str(doc.get("entityName") or "")[:200]
    cols: dict[str, list] = {f.name: [] for f in SCHEMA}
    for (tax, concept), (metric, prio) in CONCEPTS.items():
        node = (doc["facts"].get(tax) or {}).get(concept) if isinstance(doc["facts"].get(tax), dict) else None
        if not isinstance(node, dict) or not isinstance(node.get("units"), dict):
            continue
        want = UNITS.get(metric, "USD")
        for unit, facts in node["units"].items():
            if not isinstance(facts, list):
                rep.bad += 1
                continue
            if unit != want:
                rep.other_units += len(facts)
                continue
            for f in facts:
                row = _row(f, unit)
                if row is None:
                    rep.bad += 1
                    continue
                start, end, val, accn, form, fy, fp, filed = row
                cols["cik"].append(cik)
                cols["metric"].append(metric)
                cols["priority"].append(prio)
                cols["taxonomy"].append(tax)
                cols["concept"].append(concept)
                cols["unit"].append(unit)
                cols["period_start"].append(start)
                cols["period_end"].append(end)
                cols["duration_days"].append(None if start is None else (end - start).days + 1)
                cols["value"].append(val)
                cols["accn"].append(accn)
                cols["form"].append(form)
                cols["fy"].append(fy)
                cols["fp"].append(fp)
                cols["filed"].append(filed)
                cols["available_date"].append(filed + AVAILABLE_LAG)
                cols["first_seen_ts"].append(seen_ts)
    t = pa.table(cols, schema=SCHEMA)
    t = dedupe(t)
    rep.rows = t.num_rows
    return t, rep


def _row(f: object, unit: str):
    if not isinstance(f, dict):
        return None
    end = _date(f.get("end"))
    start = _date(f.get("start")) if f.get("start") is not None else None
    filed = _date(f.get("filed"))
    accn, form, val = f.get("accn"), f.get("form"), f.get("val")
    if end is None or filed is None or (f.get("start") is not None and start is None):
        return None
    if start is not None and not (start <= end and (end - start).days <= 400):
        return None
    if filed < FIRST_FILED or filed > date.today() + timedelta(days=2) or filed < end - timedelta(days=1):
        return None  # a value cannot be filed before its period ended (cover-page share counts: end ~ filed)
    if not (isinstance(accn, str) and _ACC.match(accn)) or not (isinstance(form, str) and _FORM.match(form)):
        return None
    if isinstance(val, bool) or not isinstance(val, int | float) or not math.isfinite(val) or abs(val) > 1e16:
        return None
    if unit == "shares" and val < 0:
        return None
    fy = f.get("fy")
    fy = fy if isinstance(fy, int) and not isinstance(fy, bool) and 1990 <= fy <= 2100 else None
    fp = f.get("fp")
    fp = fp if isinstance(fp, str) and _FP.match(fp) else None
    return start, end, float(val), accn, form, fy, fp, filed


def dedupe(t: pa.Table) -> pa.Table:
    """One row per filed version (VERSION_KEY), keeping the earliest first_seen_ts; sorted for stable files."""
    if t.num_rows == 0:
        return t
    t = t.sort_by([("first_seen_ts", "ascending")])
    g = t.group_by(VERSION_KEY, use_threads=False).aggregate([("first_seen_ts", "min"), ("metric", "first"),
                                                              ("priority", "first"), ("duration_days", "first"),
                                                              ("form", "first"), ("fy", "first"), ("fp", "first"),
                                                              ("filed", "first"), ("available_date", "first")])
    g = g.rename_columns([c.removesuffix("_min").removesuffix("_first") for c in g.column_names])
    g = g.select(SCHEMA.names).cast(SCHEMA)
    return g.sort_by([("metric", "ascending"), ("period_end", "ascending"), ("filed", "ascending"),
                      ("accn", "ascending"), ("concept", "ascending")])


# ---------------------------------------------------------------- the on-disk store


def facts_path(root: Path, cik: str) -> Path:
    return Path(root) / "facts" / f"cik={cik10(cik)}.parquet"


def merge_into_store(root: Path, cik: str, fresh: pa.Table) -> tuple[int, int]:
    """Union `fresh` with what is stored for `cik`: no version is ever dropped (a later download that no longer
    carries an old version keeps it). Returns (rows stored, new versions)."""
    p = facts_path(root, cik)
    old = pq.read_table(p, schema=SCHEMA) if p.is_file() else SCHEMA.empty_table()
    both = dedupe(pa.concat_tables([old, fresh.cast(SCHEMA)]))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    pq.write_table(both, tmp)
    tmp.replace(p)
    return both.num_rows, both.num_rows - old.num_rows


def gunzip_bounded(data: bytes, max_bytes: int = MAX_BYTES) -> bytes:
    """Decompress gzip without letting a hostile stream grow past `max_bytes`."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(data, max_bytes + 1)
    if len(out) > max_bytes or d.unconsumed_tail:
        raise ValueError(f"gzip body expands past {max_bytes} bytes")
    return out + d.flush()


def write_raw(root: Path, cik: str, body: bytes) -> Path:
    """Cache the raw JSON (gzip) so the parquet can be rebuilt offline; atomic."""
    p = Path(root) / "raw" / "companyfacts" / f"CIK{cik10(cik)}.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(body if body[:2] == b"\x1f\x8b" else gzip.compress(body, mtime=0))
    tmp.replace(p)
    return p


def read_raw(path: Path) -> bytes:
    return gunzip_bounded(Path(path).read_bytes())


# ---------------------------------------------------------------- ticker <-> CIK, point in time


CIK_SCHEMA = pa.schema([
    ("company", pa.string()),  # the company id of ingest/sp500/changes.csv (DOW#2017, META, ...)
    ("cik", pa.string()),
    ("ticker", pa.string()),  # the index ticker in force on valid_from <= d < valid_to
    ("valid_from", pa.date32()),
    ("valid_to", pa.date32()),  # exclusive; null = still in force
    ("how", pa.string()),  # how the CIK was found (earnings_sec.resolve: override | ticker | ticker_former | name)
    ("verified", pa.bool_()),
])


def ticker_spells(m, mapping: Iterable) -> pa.Table:
    """(company, cik, ticker, valid_from, valid_to) for every company of the point-in-time S&P 500 log with a CIK.

    `m` is an `sp500_pit.Membership`; `mapping` rows have .company, .cik, .how, .verified (earnings_sec.Mapping_).
    The ticker on a date is the index ticker then in force (FB until June 2022, then META); outside the company's
    membership spells there is no ticker row (look such a company up by CIK)."""
    members: dict[date, set[str]] = {}

    def ticker_on(company: str, sp, d: date) -> str:  # earnings_sec.ticker_on for d inside spell sp, memoised
        if d not in members:
            members[d] = m.members_on(d)
        for t in reversed(sp.tickers):
            if t in members[d] and m.company_of(t, d) == company:
                return t
        return sp.tickers[-1]

    by_company = {r.company: r for r in mapping if r.cik}
    cols: dict[str, list] = {f.name: [] for f in CIK_SCHEMA}
    changes = _change_dates(m)
    for company, spells in sorted(m.spells.items()):
        mp = by_company.get(company)
        if mp is None:
            continue
        for sp in spells:
            bounds = sorted({sp.start, *(d for d in changes if sp.start < d and (sp.end is None or d < sp.end))})
            run: tuple[str, date] | None = None
            for d in bounds:
                t = ticker_on(company, sp, d)
                if run is None or t != run[0]:
                    if run is not None:
                        _add(cols, mp, run[0], run[1], d)
                    run = (t, d)
            if run is not None:
                _add(cols, mp, run[0], run[1], sp.end)
    return pa.table(cols, schema=CIK_SCHEMA)


def _change_dates(m) -> list[date]:
    ds = {ch.date for ch in m.changes}
    for aliases in m.aliases.values():
        ds |= {a.until + timedelta(days=1) for a in aliases}
    return sorted(ds)


def _add(cols: dict, mp, ticker: str, start: date, end: date | None) -> None:
    for k, v in (("company", mp.company), ("cik", cik10(mp.cik)), ("ticker", ticker), ("valid_from", start),
                 ("valid_to", end), ("how", mp.how), ("verified", bool(mp.verified))):
        cols[k].append(v)


def write_ciks(root: Path, table: pa.Table) -> Path:
    p = Path(root) / "ciks.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    pq.write_table(table.cast(CIK_SCHEMA), tmp)
    tmp.replace(p)
    return p


def ciks_of(table: pa.Table) -> list[str]:
    return sorted(set(pc.unique(table["cik"]).to_pylist())) if table.num_rows else []


def summary(reports: Mapping[str, ParseReport], misses: Sequence[str]) -> dict:
    return {
        "companies_with_facts": sum(1 for r in reports.values() if r.rows),
        "rows_parsed": sum(r.rows for r in reports.values()),
        "bad_rows_skipped": sum(r.bad for r in reports.values()),
        "other_unit_rows_skipped": sum(r.other_units for r in reports.values()),
        "no_facts": sorted(misses),
    }
