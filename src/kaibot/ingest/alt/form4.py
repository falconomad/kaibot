"""SEC EDGAR Form 4 insider transactions for the universe (issuers in config/universe_seed.yaml).

Discovery (both checked against live EDGAR, 10 Nov 2026):
- nightly / backfill: https://data.sec.gov/submissions/CIK##########.json per issuer. `filings.recent` holds parallel
  arrays (accessionNumber, filingDate, acceptanceDateTime in UTC, form, primaryDocument such as
  "xslF345X06/form4.xml"). The raw XML is the primaryDocument without its xsl* folder.
- intraday (optional): the current-filings Atom feed (ingest.edgar.parse_current_feed, type=4); the document name
  comes from the filing's index.json.
Each filing is the ownershipDocument XML: one row per non-derivative / derivative transaction.

Fair access: every request goes through the shared `sec_edgar` budget (5 req/s; SEC allows 10) with the declared
SEC_USER_AGENT ("Name email"). A nightly run is ~62 requests plus one per new filing.

Point-in-time (tech design §2, EDGAR row): available_ts = acceptance time; accepted at/after 17:30 ET -> the start
(00:00 ET) of the next business day. accepted_ts is kept so research can choose otherwise.
Idempotent: a ledger (alt/form4_filings) records every accession processed; it is never fetched twice.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa

from kaibot.common import http, safexml
from kaibot.ingest import edgar
from kaibot.ingest.alt import store

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
BUDGET = "sec_edgar"
SOURCE = "form4"
LEDGER = "form4_filings"
FORMS = {"4", "4/A"}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
FEED_URL = edgar.CURRENT_URL.format(form="4")
CUTOFF_ET = time(17, 30)
FLUSH_EVERY = 200
_ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOC = re.compile(r"^[A-Za-z0-9._\-]{1,100}\.xml$")
_CODE = re.compile(r"^[A-Z]$")
_UA = re.compile(r"^[\x20-\x7e]{3,200}$")

TX_SCHEMA = pa.schema([
    ("accession", pa.string()),
    ("row", pa.int32()),
    ("symbol", pa.string()),
    ("issuer_cik", pa.string()),
    ("form", pa.string()),
    ("owner_cik", pa.string()),
    ("owner_name", pa.string()),
    ("n_owners", pa.int32()),
    ("is_director", pa.bool_()),
    ("is_officer", pa.bool_()),
    ("is_ten_pct_owner", pa.bool_()),
    ("officer_title", pa.string()),
    ("aff10b5one", pa.bool_()),
    ("derivative", pa.bool_()),
    ("security_title", pa.string()),
    ("transaction_date", pa.date32()),
    ("code", pa.string()),
    ("shares", pa.float64()),
    ("price", pa.float64()),
    ("acquired_disposed", pa.string()),
    ("shares_after", pa.float64()),
    ("direct_indirect", pa.string()),
    ("filing_date", pa.date32()),
    ("accepted_ts", store.TS),
    ("available_ts", store.TS),
    ("ingested_ts", store.TS),
])
TX_KEY = ["accession", "row"]
TX_VALUES = ["code", "shares", "price", "transaction_date"]
LEDGER_SCHEMA = pa.schema([
    ("accession", pa.string()),
    ("symbol", pa.string()),
    ("issuer_cik", pa.string()),
    ("form", pa.string()),
    ("filing_date", pa.date32()),
    ("status", pa.string()),  # ok | no_transactions | not_issuer | invalid
    ("n_rows", pa.int32()),
    ("accepted_ts", store.TS),
    ("available_ts", store.TS),
    ("ingested_ts", store.TS),
])


class NotIssuer(ValueError):
    """The filing lists our company as reporting OWNER of another issuer (e.g. a bank's 10% stakes): not ours."""


@dataclass(frozen=True)
class Candidate:
    accession: str
    symbol: str
    cik: str  # 10 digits
    form: str
    filing_date: date
    accepted: datetime  # UTC
    doc: str | None  # raw XML file name in the filing folder, None = look it up in index.json


def available_ts(accepted: datetime) -> datetime:
    et = accepted.astimezone(ET)
    if et.time() < CUTOFF_ET:
        return accepted.astimezone(UTC)
    d = et.date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, time(0, 0), ET).astimezone(UTC)


def check_user_agent(ua: str | None) -> str:
    if not ua or not _UA.match(ua) or "@" not in ua:
        raise SystemExit("SEC_USER_AGENT must be set to 'Name contact@email' (SEC fair-access policy)")
    return ua


def _headers(ua: str) -> dict[str, str]:
    return {"User-Agent": ua, "Accept-Encoding": "identity"}


# --- parsing (all inputs untrusted) -------------------------------------------------------------------------


def parse_tickers(doc: object) -> dict[str, str]:
    """company_tickers.json -> {TICKER: 10-digit CIK}; skips malformed entries."""
    if not isinstance(doc, dict):
        raise ValueError("company_tickers: expected an object")
    out = {}
    for v in doc.values():
        if isinstance(v, dict) and isinstance(v.get("cik_str"), int) and isinstance(v.get("ticker"), str):
            out[v["ticker"].upper()] = str(v["cik_str"]).zfill(10)
    return out


def universe_ciks(symbols: list[str], tickers: dict[str, str]) -> dict[str, str]:
    out = {}
    for s in symbols:
        c = tickers.get(s.upper()) or tickers.get(s.upper().replace(".", "-"))
        if c:
            out[s] = c
        else:
            log.warning("form4: no CIK for %s", s)
    return out


def _parse_utc(s: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t.astimezone(UTC) if t.tzinfo else None


def candidates_from_submissions(doc: object, symbol: str, cik: str, start: date, end: date) -> list[Candidate]:
    if not isinstance(doc, dict) or str(doc.get("cik", "")).zfill(10) != cik:
        raise ValueError(f"submissions for {symbol}: wrong or missing cik")
    recent = (doc.get("filings") or {}).get("recent")
    cols = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "primaryDocument")
    if not isinstance(recent, dict) or not all(isinstance(recent.get(c), list) for c in cols):
        raise ValueError(f"submissions for {symbol}: unexpected schema")
    n = len(recent["accessionNumber"])
    if any(len(recent[c]) != n for c in cols):
        raise ValueError(f"submissions for {symbol}: ragged arrays")
    out = []
    for acc, fd, at, form, pdoc in zip(*(recent[c] for c in cols), strict=True):
        if form not in FORMS:
            continue
        try:
            fdate = date.fromisoformat(fd)
        except (TypeError, ValueError):
            continue
        if not start <= fdate <= end:
            continue
        accepted = _parse_utc(at) if isinstance(at, str) else None
        doc_name = pdoc.rsplit("/", 1)[-1] if isinstance(pdoc, str) else ""
        if not (isinstance(acc, str) and _ACC.match(acc)) or accepted is None or not _DOC.match(doc_name):
            log.warning("form4 %s: skipping malformed submissions row %r", symbol, acc)
            continue
        out.append(Candidate(acc, symbol, cik, form, fdate, accepted, doc_name))
    return out


def candidates_from_feed(xml: bytes | str, by_cik: dict[str, str]) -> list[Candidate]:
    out = []
    for f in edgar.parse_current_feed(xml):
        if f.form in FORMS and f.cik in by_cik and _ACC.match(f.accession):
            accepted = f.accepted.astimezone(UTC)
            out.append(Candidate(f.accession, by_cik[f.cik], f.cik, f.form, f.accepted.astimezone(ET).date(),
                                 accepted, None))
    return out


def doc_from_index(doc: object) -> str | None:
    items = ((doc or {}).get("directory") or {}).get("item") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return None
    names = [i.get("name") for i in items if isinstance(i, dict) and isinstance(i.get("name"), str)]
    xml = [n for n in names if _DOC.match(n)]
    return xml[0] if len(xml) == 1 else None


def _text(el, path: str) -> str | None:
    if el is None:
        return None
    v = el.findtext(path)
    v = v.strip() if v else None
    return v[:200] if v else None


def _num(el, path: str) -> float | None:
    v = _text(el, path)
    if v is None:
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return x if math.isfinite(x) and x >= 0 else None


def _flag(el, path: str) -> bool | None:
    v = (_text(el, path) or "").lower()
    return True if v in ("1", "true") else False if v in ("0", "false") else None


def _date(v: str | None) -> date | None:
    try:
        return date.fromisoformat(v[:10]) if v else None
    except ValueError:
        return None


def parse_form4(xml: bytes | str, expected_cik: str) -> tuple[dict, list[dict]]:
    """Return (filing info, transaction rows). Raises ValueError for a document that is not this issuer's Form 4."""
    root = safexml.fromstring(xml, max_bytes=4 * 1024 * 1024)
    if root.tag != "ownershipDocument":
        raise ValueError(f"not an ownershipDocument: <{root.tag[:40]}>")
    form = _text(root, "documentType")
    if form not in FORMS:
        raise ValueError(f"documentType {form!r} is not a Form 4")
    issuer_cik = (_text(root, "issuer/issuerCik") or "").zfill(10)
    if issuer_cik != expected_cik:
        raise NotIssuer(f"issuer CIK {issuer_cik} != expected {expected_cik}")
    owners = root.findall("reportingOwner")
    o = owners[0] if owners else None
    info = {
        "form": form,
        "issuer_cik": issuer_cik,
        "owner_cik": (_text(o, "reportingOwnerId/rptOwnerCik") or "").zfill(10) if o is not None else None,
        "owner_name": _text(o, "reportingOwnerId/rptOwnerName"),
        "n_owners": len(owners),
        "is_director": _flag(o, "reportingOwnerRelationship/isDirector"),
        "is_officer": _flag(o, "reportingOwnerRelationship/isOfficer"),
        "is_ten_pct_owner": _flag(o, "reportingOwnerRelationship/isTenPercentOwner"),
        "officer_title": _text(o, "reportingOwnerRelationship/officerTitle"),
        "aff10b5one": _flag(root, "aff10b5One"),
    }
    rows = []
    for derivative, path in ((False, "nonDerivativeTable/nonDerivativeTransaction"),
                             (True, "derivativeTable/derivativeTransaction")):
        for t in root.findall(path):
            code = _text(t, "transactionCoding/transactionCode")
            shares = _num(t, "transactionAmounts/transactionShares/value")
            tdate = _date(_text(t, "transactionDate/value"))
            if not code or not _CODE.match(code) or shares is None or tdate is None:
                log.warning("form4 %s: skipping malformed transaction (code=%r)", issuer_cik, code)
                continue
            rows.append({
                "derivative": derivative,
                "security_title": _text(t, "securityTitle/value"),
                "transaction_date": tdate,
                "code": code,
                "shares": shares,
                "price": _num(t, "transactionAmounts/transactionPricePerShare/value"),
                "acquired_disposed": _text(t, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                "shares_after": _num(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
                "direct_indirect": _text(t, "ownershipNature/directOrIndirectOwnership/value"),
            })
    return info, rows


# --- collection ---------------------------------------------------------------------------------------------


def ticker_map(root: Path, user_agent: str, max_age_days: int = 7, **http_kw) -> dict[str, str]:
    """SEC ticker -> CIK map, cached under data/alt/_meta/ and refreshed weekly (one ~0.8 MB request)."""
    cache = Path(root) / "alt" / "_meta" / "sec_company_tickers.json"
    fresh = cache.exists() and (datetime.now(UTC).timestamp() - cache.stat().st_mtime) < max_age_days * 86400
    if not fresh:
        r = http.get(edgar.TICKERS_URL, source=BUDGET, headers=_headers(check_user_agent(user_agent)), timeout=30,
                     max_bytes=8 * 1024 * 1024, **http_kw)
        parse_tickers(json.loads(r.body))  # validate before caching
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(r.body)
        os.replace(tmp, cache)
    return parse_tickers(json.loads(cache.read_bytes()))


def seen_accessions(root: Path) -> set[str]:
    df = store.read_source(root, LEDGER, columns=["accession"])
    return set(df["accession"]) if not df.empty else set()


class _Batch:
    """Collects one run's rows and writes one part per partition at the end (few, larger Parquet files)."""

    def __init__(self, now: datetime):
        self.now = now
        self.tx: dict[date, list[dict]] = {}
        self.ledger: dict[date, list[dict]] = {}

    def add(self, c: Candidate, status: str, info: dict, rows: list[dict]) -> None:
        avail = available_ts(c.accepted)
        common = {k: info.get(k) for k in ("form", "owner_cik", "owner_name", "n_owners", "is_director",
                                            "is_officer", "is_ten_pct_owner", "officer_title", "aff10b5one")}
        self.tx.setdefault(c.filing_date, []).extend(
            {**common, **r, "accession": c.accession, "row": i, "symbol": c.symbol, "issuer_cik": c.cik,
             "filing_date": c.filing_date, "accepted_ts": c.accepted, "available_ts": avail, "ingested_ts": self.now}
            for i, r in enumerate(rows))
        self.ledger.setdefault(c.filing_date, []).append({
            "accession": c.accession, "symbol": c.symbol, "issuer_cik": c.cik, "form": c.form,
            "filing_date": c.filing_date, "status": status, "n_rows": len(rows), "accepted_ts": c.accepted,
            "available_ts": avail, "ingested_ts": self.now,
        })

    def flush(self, root: Path) -> int:
        n = 0
        for day, recs in self.tx.items():  # transactions first: the ledger marks an accession as done
            if recs:
                n += store.append_versions(root, SOURCE, day, pa.Table.from_pylist(recs, schema=TX_SCHEMA),
                                           TX_KEY, TX_VALUES)
        for day, recs in self.ledger.items():
            store.append_versions(root, LEDGER, day, pa.Table.from_pylist(recs, schema=LEDGER_SCHEMA),
                                  ["accession"], ["status"])
        self.tx.clear()
        self.ledger.clear()
        return n


def collect(
    root: Path,
    universe: dict[str, str],
    user_agent: str,
    start: date,
    end: date,
    *,
    mode: str = "submissions",
    now: datetime | None = None,
    **http_kw,
) -> int:
    """Fetch new Form 4 filings for `universe` ({symbol: 10-digit CIK}) filed in [start, end]. Returns rows written."""
    now = now or datetime.now(UTC)
    h = _headers(check_user_agent(user_agent))
    seen = seen_accessions(root)
    cands: list[Candidate] = []
    if mode == "submissions":
        for sym, cik in sorted(universe.items()):
            try:
                doc = http.get_json(SUBMISSIONS_URL.format(cik=cik), source=BUDGET, headers=h, timeout=30,
                                    max_bytes=16 * 1024 * 1024, **http_kw)
                cands += candidates_from_submissions(doc, sym, cik, start, end)
            except (http.HttpError, ValueError) as e:
                log.error("form4 %s: submissions failed: %s", sym, e)
    elif mode == "feed":
        r = http.get(FEED_URL, source=BUDGET, headers=h, timeout=30, max_bytes=4 * 1024 * 1024, **http_kw)
        cands = [c for c in candidates_from_feed(r.body, {v: k for k, v in universe.items()})
                 if start <= c.filing_date <= end]
    else:
        raise ValueError(f"unknown mode {mode!r}")
    batch = _Batch(now)
    todo = list({c.accession: c for c in cands if c.accession not in seen}.values())
    written = 0
    for i, c in enumerate(todo):
        if i and i % FLUSH_EVERY == 0:  # a long backfill keeps its progress if interrupted
            written += batch.flush(root)
        acc = c.accession.replace("-", "")
        cik_int = str(int(c.cik))
        try:
            doc_name = c.doc
            if doc_name is None:
                idx = http.get_json(ARCHIVE_URL.format(cik=cik_int, acc=acc, doc="index.json"), source=BUDGET,
                                    headers=h, timeout=30, max_bytes=1024 * 1024, **http_kw)
                doc_name = doc_from_index(idx)
                if doc_name is None:
                    raise ValueError("no single XML document in the filing index")
            r = http.get(ARCHIVE_URL.format(cik=cik_int, acc=acc, doc=doc_name), source=BUDGET, headers=h,
                         timeout=30, max_bytes=4 * 1024 * 1024, **http_kw)
        except http.HttpError as e:
            log.error("form4 %s %s: fetch failed, will retry next run: %s", c.symbol, c.accession, e)
            continue
        except ValueError as e:
            log.error("form4 %s %s: %s", c.symbol, c.accession, e)
            batch.add(c, "invalid", {}, [])
            continue
        try:
            info, rows = parse_form4(r.body, c.cik)
        except NotIssuer as e:
            log.info("form4 %s %s: %s is the reporting owner here, not the issuer: skipped", c.symbol,
                     c.accession, c.symbol)
            log.debug("%s", e)
            batch.add(c, "not_issuer", {}, [])
            continue
        except ValueError as e:  # includes safexml.UnsafeXML
            log.error("form4 %s %s: invalid document: %s", c.symbol, c.accession, e)
            batch.add(c, "invalid", {}, [])
            continue
        batch.add(c, "ok" if rows else "no_transactions", info, rows)
    written += batch.flush(root)
    log.info("form4 %s..%s: %d candidates, %d new, %d rows written", start, end, len(cands), len(todo), written)
    return written
