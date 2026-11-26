"""New-listing watch (story S102, #376): a nightly SHADOW collector of new US listings from three free inputs.

It writes a store. The engine never reads it, and it changes no order, no rule and no decision
(tests/unit/test_listings.py holds the isolation test; `kaibot.ingest.listings_pit` is the reader).

Inputs (design for M8, section 3.1):

1. Alpaca assets: GET /v2/assets?status=active&asset_class=us_equity on the PAPER host, one request a night under
   the `alpaca_trading` budget. Alpaca gives no listing date, so tonight's list is compared with what the store
   already knows: a new asset id is `first_seen`, and that first sighting is Kaibot's own date. The first night is
   a baseline (`baseline` True): nothing in it is new.
2. SEC EDGAR daily index (www.sec.gov/Archives/edgar/daily-index/YYYY/QTRn/master.YYYYMMDD.idx, built each night
   from about 22:00 ET): forms S-1, S-1/A, F-1, F-1/A (planned), 8-A12B (exchange registration, shortly before
   listing), 424B4 (final prospectus after pricing) and 10-12B (spin-off), with company, CIK and filing date. For a
   company with a fresh 8-A12B or 424B4, its submissions file adds the acceptance time and the ticker, and the top
   of a 424B4 is read for the offer price and the share count. Under the shared `sec_edgar` budget with the
   declared SEC_USER_AGENT.
3. Nasdaq Trader: the symbol directory (nasdaqlisted.txt, otherlisted.txt) and the "Data Technical News" feed, in
   which Nasdaq announces large listings days ahead. At most one request a second (`nasdaq_trader`).
4. For a symbol that looks like a new listing: its first daily bar from Alpaca (one request, `alpaca` budget, SIP
   data at least 15 minutes old). The bar confirms the first session, or shows that the symbol traded before.

Store: data/alt/listings/date=YYYY-MM-DD/part-*.parquet, append-only, one row per observation (`SCHEMA`).
Point in time: `available_ts` is the first time Kaibot could have known the row. For a sighting (Alpaca, symbol
directory, a ticker read from a submissions file) that is the collection time. For a published item it is the
publication: the time the daily index existed, a notice's publication time, a session's close plus 15 minutes for
its bar. `ingested_ts` is when this machine stored the row; a row collected late has a later `ingested_ts` than
`available_ts`, and `listings_pit` can read either way.

NOT VERIFIED against a live response (the sandbox this was built in reaches none of the three): the layout of the
daily index, EDGAR's answer for a day without an index, the symbol directory's URL, the feed's layout and the
wording of a notice or a prospectus cover. Every parser therefore fails soft: what it cannot read stays empty and
is logged, one input failing does not stop the others, and nothing is guessed.
"""

from __future__ import annotations

import email.utils
import html
import json
import logging
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa

from kaibot.common import http, safexml
from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.alt import store

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SOURCE = "listings"
BUDGET_ASSETS, BUDGET_BARS, BUDGET_SEC, BUDGET_NASDAQ = "alpaca_trading", "alpaca", "sec_edgar", "nasdaq_trader"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"  # paper only: Kaibot has no live endpoint
BARS_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/master.{ymd}.idx"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/{path}"
SYMDIR_URLS = {"nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
               "otherlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"}
NOTICES_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=currentheadlines&categorylist=48"  # Data Technical News
NOTICE_HOSTS = ("https://www.nasdaqtrader.com/", "https://nasdaqtrader.com/")

PLAN_FORMS = frozenset({"S-1", "S-1/A", "F-1", "F-1/A"})
REGISTRATION, PROSPECTUS = "8-A12B", "424B4"
SPIN_FORMS = frozenset({"10-12B", "10-12B/A"})
FORMS = PLAN_FORMS | SPIN_FORMS | {REGISTRATION, PROSPECTUS}
INDEX_BUILD_ET = time(22, 0)  # "updated nightly starting about 10:00 p.m., ET" (SEC, Accessing EDGAR Data)
INDEX_DONE_ET = time(6, 0)  # "usually completed within a few hours": taken as done by the next morning
FIRST_RUN_DAYS, LOOKBACK_DAYS = 120, 7  # daily indexes read on the first run / re-checked every night
ENRICH_DAYS, TICKER_RETRY_DAYS, MAX_ENRICH = 30, 10, 400
MAX_PROSPECTUS, COVER_BYTES = 60, 200_000  # a cover page sits at the top of the filing's text file
MAX_NOTICE_PAGES = 5
BAR_WINDOW_DAYS, BAR_OLD_DAYS, MAX_BAR_LOOKUPS, MAX_BAR_TRIES = 45, 7, 40, 15
SIP_DELAY = timedelta(minutes=20)  # free plan: SIP only when at least 15 minutes old
BAR_DELAY = timedelta(minutes=15)
SHRINK_LIMIT = 0.5  # a list less than half as long as last night's is a broken answer, not 6,000 delistings
UNLISTED = frozenset({"OTC"})  # stored, but a symbol that appears there is not a listing on an exchange

SCHEMA = pa.schema([
    ("key", pa.string()), ("source", pa.string()), ("kind", pa.string()), ("symbol", pa.string()),
    ("company", pa.string()), ("cik", pa.string()), ("form", pa.string()), ("accession", pa.string()),
    ("filed", pa.date32()), ("first_trade_date", pa.date32()), ("exchange", pa.string()), ("status", pa.string()),
    ("tradable", pa.bool_()), ("attributes", pa.string()), ("etf", pa.bool_()), ("test", pa.bool_()),
    ("offer_price", pa.float64()), ("shares_offered", pa.float64()), ("pricing_date", pa.date32()),
    ("url", pa.string()), ("baseline", pa.bool_()), ("accepted_ts", store.TS), ("published_ts", store.TS),
    ("available_ts", store.TS), ("ingested_ts", store.TS)])
KEY = ["key"]
VALUES = ["kind", "symbol", "company", "exchange", "status", "tradable", "attributes", "etf", "test",
          "first_trade_date", "offer_price", "shares_offered", "pricing_date", "accepted_ts"]

_ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_PATH = re.compile(r"^edgar/data/\d{1,10}/\d{10}-\d{2}-\d{6}\.txt$")
_SYMBOL = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z]{1,2})?$")  # what may become a URL path segment
_ANY_SYMBOL = re.compile(r"^[A-Za-z0-9.\-+/$^=#]{1,14}$")
_UA = re.compile(r"^[\x20-\x7e]{3,200}$")
_LISTING = re.compile(r"initial public offering|\bIPO\b|direct listing", re.I)
_SENTENCE = re.compile(r"symbol\s+[\"“'‘]?([A-Z]{1,5}(?:\.[A-Z]{1,2})?)[\"”'’]?[\s,]+on\s+"
                       r"(?:[A-Z][a-z]+day,\s+)?([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")
_SYMBOL_ONLY = re.compile(r"ticker\s+symbol\s+[\"“'‘]?([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b")
_LONG_DATE = re.compile(r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")
_NOTICE_COMPANY = re.compile(r"#\s*\d{4}\s*-\s*\d+\s+(.*?)\s+(?:Initial Public Offering|Direct Listing|IPO)\b", re.I)
_PRICE = re.compile(r"public\s+offering\s+price[^$]{0,80}\$\s?(\d[\d,]*(?:\.\d+)?)", re.I)
_SHARES = re.compile(r"(?:^|(?:offering|selling)\s+)(\d[\d,]{3,})\s+(?:shares|american\s+depositary\s+shares)",
                     re.I | re.M)
_COVER_DATE = re.compile(r"date\s+of\s+this\s+prospectus\s+is\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", re.I)
_NOT_COMMON = re.compile(r"\b(ETFS?|ETNS?|FUND|UNITS?|WARRANTS?|RIGHTS?|PREFERRED|NOTES?|DEBENTURES?)\b|\d%", re.I)


class InputsFailed(RuntimeError):
    """Some inputs failed tonight. Raised after everything that worked was stored."""

    def __init__(self, failed: dict[str, str], written: int):
        super().__init__("listings: " + "; ".join(f"{k}: {v}" for k, v in failed.items()))
        self.failed, self.written = failed, written


@dataclass(frozen=True)
class Creds:
    """Read from the env file at run time by the caller; never logged."""

    alpaca_key: str
    alpaca_secret: str
    sec_user_agent: str


def _row(key: str, source: str, kind: str, **kw) -> dict:
    row = dict.fromkeys(SCHEMA.names)
    row.update(key=key, source=source, kind=kind, symbol="", company="", cik="", form="", accession="", exchange="",
               status="", attributes="", url="", baseline=False)
    row.update(kw)
    return row


def _et_date(ts: datetime) -> date:
    return ts.astimezone(ET).date()


def _long_date(s: str) -> date | None:
    try:
        return datetime.strptime(" ".join(s.replace(",", ", ").split()), "%B %d, %Y").date()
    except ValueError:
        return None


def strip_tags(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/tr|/h\d|/li)\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]{0,2000}>", " ", text))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


# ---------------------------------------------------------------- parsing (every input is untrusted)


def parse_assets(doc: object) -> list[dict]:
    """Alpaca's asset list -> US equities, sorted by symbol. Malformed entries are skipped."""
    if not isinstance(doc, list):
        raise ValueError("assets: expected a list")
    out = {}
    for a in doc:
        if not isinstance(a, dict) or a.get("class") != "us_equity":
            continue
        aid, sym, name = a.get("id"), a.get("symbol"), a.get("name")
        if not (isinstance(aid, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", aid) and isinstance(sym, str)
                and _ANY_SYMBOL.match(sym) and isinstance(name, str)):
            continue
        attrs = a.get("attributes") if isinstance(a.get("attributes"), list) else []
        out[aid] = {"id": aid.lower(), "symbol": sym.upper(), "company": name[:300],
                    "exchange": str(a.get("exchange") or "")[:20], "status": str(a.get("status") or "")[:20],
                    "tradable": a.get("tradable") is True,
                    "attributes": ",".join(sorted(str(x)[:40] for x in attrs if isinstance(x, str)))}
    return sorted(out.values(), key=lambda r: (r["symbol"], r["id"]))


# The column line of EDGAR's master index. The two indexes spell its last column differently: the daily index
# (daily-index/.../master.YYYYMMDD.idx) writes "File Name", the quarterly one (full-index/YYYY/QTRn/master.idx)
# writes "Filename" (#376: a check for "File Name" alone refused every quarter since 2016).
_INDEX_COLUMNS = re.compile(r"^CIK\|Company Name\|Form Type\|Date Filed\|(?:File Name|Filename)[ \t]*\r?$", re.M)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{24,}")
ANSWER_CHARS = 80


def describe_answer(r: http.Response) -> str:
    """What a server answered, for an error message: status, content type, size and the first 80 characters,
    whitespace collapsed, e-mail addresses and long tokens redacted, anything unprintable replaced."""
    text = " ".join(r.body[:2000].decode("latin-1").split())
    text = _TOKEN.sub("<redacted>", _EMAIL.sub("<email>", text))
    text = "".join(c if c.isprintable() else "?" for c in text)[:ANSWER_CHARS]
    ctype = "".join(c for c in r.content_type if c.isprintable())[:60] or "no content-type"
    return f"HTTP {r.status}, {ctype}, {len(r.body)} bytes, starts {text!r}"


def parse_master_index(text: str, forms: Iterable[str] = FORMS) -> list[dict]:
    """EDGAR's master index (`CIK|Company Name|Form Type|Date Filed|File Name`, the quarterly one ending in
    `Filename`) -> the rows of `forms`. The daily index writes the date as YYYYMMDD, the quarterly one as
    YYYY-MM-DD; both are read. Other lines are skipped; a text without the column line is not an index
    (ValueError)."""
    if not _INDEX_COLUMNS.search(text[:4000]):
        raise ValueError("not an EDGAR master index (no column line)")
    forms = frozenset(forms)
    out = []
    for line in text.splitlines():
        f = line.rstrip().split("|")
        if len(f) != 5 or f[2] not in forms or not f[0].isdigit() or len(f[0]) > 10 or not _PATH.match(f[4]):
            continue
        d = f[3].strip()
        try:
            filed = date.fromisoformat(d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:8]}")
        except ValueError:
            continue
        out.append({"cik": f[0].zfill(10), "company": f[1].strip()[:300], "form": f[2], "filed": filed,
                    "accession": f[4].rsplit("/", 1)[-1][:-4], "path": f[4]})
    return out


def master_index_rows(r: http.Response, forms: Iterable[str] = FORMS) -> list[dict]:
    """`parse_master_index` of a response; when it is not an index, the error says what SEC answered instead."""
    try:
        return parse_master_index(r.body.decode("latin-1"), forms)
    except ValueError as e:
        raise ValueError(f"{e}; SEC answered {describe_answer(r)}") from None


def index_floor(day: date) -> datetime:
    return datetime.combine(day, INDEX_BUILD_ET, ET).astimezone(UTC)


def index_available_ts(day: date, now: datetime, last_modified: datetime | None) -> datetime:
    """The first time the daily index of `day` could have been read: never before the nightly build starts
    (22:00 ET); the file's Last-Modified when the server gives one; else the collection time when it was read
    that night, and 06:00 ET the next morning when it was read later."""
    floor = index_floor(day)
    if last_modified is not None:
        return max(floor, last_modified.astimezone(UTC))
    done = datetime.combine(day + timedelta(days=1), INDEX_DONE_ET, ET).astimezone(UTC)
    return max(floor, min(now.astimezone(UTC), done))


def index_days(now: datetime, done: set[date], lookback_days: int) -> list[date]:
    """Weekdays of the last `lookback_days` whose index can exist by `now` and is not in `done`."""
    today = _et_date(now)
    days = (today - timedelta(days=i) for i in range(lookback_days, -1, -1))
    return [d for d in days if d.weekday() < 5 and d not in done and now >= index_floor(d)]


def parse_symdir(text: str) -> tuple[list[dict], datetime | None]:
    """A Nasdaq Trader symbol directory file -> (rows, file creation time). Columns are found by name, so an
    added column does not break it. Rows: symbol, company (the security name), etf, test."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    head = lines[0].split("|") if lines else []
    sym = next((c for c in ("Symbol", "ACT Symbol") if c in head), None)
    if sym is None or "Security Name" not in head or "Test Issue" not in head:
        raise ValueError(f"not a symbol directory file (header {lines[0][:80]!r})" if lines else "empty file")
    idx = {c: head.index(c) for c in head}
    rows, made = {}, None
    for ln in lines[1:]:
        f = ln.split("|")
        if f[0].startswith("File Creation Time"):
            m = re.search(r"(\d{2})(\d{2})(\d{4})(\d{2}):?(\d{2})", f[0])
            if m:
                mo, dd, yy, hh, mi = (int(x) for x in m.groups())
                try:
                    made = datetime(yy, mo, dd, hh, mi, tzinfo=ET).astimezone(UTC)
                except ValueError:
                    made = None
            continue
        if len(f) != len(head) or not _ANY_SYMBOL.match(f[idx[sym]]):
            continue
        rows[f[idx[sym]].upper()] = {"symbol": f[idx[sym]].upper(), "company": f[idx["Security Name"]][:300],
                                     "etf": "ETF" in idx and f[idx["ETF"]] == "Y", "test": f[idx["Test Issue"]] == "Y"}
    return [rows[k] for k in sorted(rows)], made


def parse_notices(xml: bytes | str) -> list[dict]:
    """The news feed (RSS 2.0) -> the items that announce a listing: title, url, published (UTC), description."""
    root = safexml.fromstring(xml, max_bytes=4 * 1024 * 1024)
    if root.tag != "rss":
        raise ValueError(f"not an RSS feed: <{root.tag[:40]}>")
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()[:300]
        url = (item.findtext("link") or "").strip()[:300]
        if not _LISTING.search(title) or not url.startswith(NOTICE_HOSTS):
            continue
        try:
            published = email.utils.parsedate_to_datetime((item.findtext("pubDate") or "").strip())
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            continue
        out.append({"title": title, "url": url, "published": published.astimezone(UTC),
                    "description": strip_tags(item.findtext("description") or "")[:4000]})
    return out


def notice_company(title: str) -> str:
    m = _NOTICE_COMPANY.search(title)
    return m.group(1).strip() if m else ""


def parse_notice_text(text: str) -> dict:
    """What a notice says about a listing: the ticker symbol, the first trading date, and whether it is an
    initial public offering or a direct listing. Anything it does not say in the expected words stays empty."""
    kind = "direct_listing" if re.search(r"direct listing", text, re.I) else (
        "ipo" if re.search(r"initial public offering|\bIPO\b", text, re.I) else "")
    flat = " ".join(text.split())
    m = _SENTENCE.search(flat)
    if m:
        return {"symbol": m.group(1), "first_trade_date": _long_date(m.group(2)), "kind": kind}
    m = _SYMBOL_ONLY.search(flat)
    return {"symbol": m.group(1) if m else "", "first_trade_date": None, "kind": kind}


def parse_prospectus_cover(markup: str) -> dict:
    """Offer price, shares offered and the prospectus date from the top of a 424B4. A value is returned only when
    the cover states exactly one: two different prices (a range, an assumed price) or two share counts (a
    company and selling holders) leave it empty. Never guessed."""
    text = strip_tags(markup[:COVER_BYTES * 2])[:60_000]
    prices = {float(p.replace(",", "")) for p in _PRICE.findall(text)}
    shares = {float(s.replace(",", "")) for s in _SHARES.findall(text)}
    d = _COVER_DATE.search(" ".join(text.split()))
    price = prices.pop() if len(prices) == 1 else None
    return {"offer_price": price if price is not None and 0.01 <= price <= 100_000 else None,
            "shares_offered": shares.pop() if len(shares) == 1 else None,
            "prospectus_date": _long_date(d.group(1)) if d else None}


# ---------------------------------------------------------------- state


def latest(events: pd.DataFrame) -> pd.DataFrame:
    """The newest version of every observation."""
    if events.empty:
        return events
    e = events.sort_values(["available_ts", "ingested_ts"], kind="stable")
    return e.drop_duplicates("key", keep="last").reset_index(drop=True)


def _state(root: Path) -> dict[str, dict]:
    df = latest(store.read_source(root, SOURCE))
    if df.empty:
        return {}
    df = df.astype(object).where(df.notna(), None)
    return {r["key"]: r for r in df.to_dict("records")}


def _meta_path(root: Path) -> Path:
    return Path(root) / "alt" / "_meta" / "listings_state.json"


def _read_meta(root: Path) -> dict:
    try:
        doc = json.loads(_meta_path(root).read_text())
    except (OSError, ValueError):
        doc = {}
    return {k: dict(doc.get(k) or {}) for k in ("index_days", "prospectus", "notice_pages", "bar_tries")}


def _write_meta(root: Path, meta: dict) -> None:
    p = _meta_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=1, sort_keys=True))
    os.replace(tmp, p)


def _write(root: Path, rows: Sequence[dict], now: datetime, partition) -> int:
    """Append rows, one part per partition day. `partition(row)` is the day the observation belongs to."""
    by_day: dict[date, list[dict]] = {}
    for r in rows:
        r["ingested_ts"] = now
        by_day.setdefault(partition(r), []).append(r)
    n = 0
    for day, recs in sorted(by_day.items()):
        table = pa.Table.from_pylist(recs, schema=SCHEMA)
        n += store.append_versions(root, SOURCE, day, table, KEY, VALUES, revision_available_ts=now)
    return n


# ---------------------------------------------------------------- input 1: Alpaca assets


def _alpaca_headers(creds: Creds) -> dict[str, str]:
    if not creds.alpaca_key or not creds.alpaca_secret:
        raise ValueError("no Alpaca paper keys in the env file (ALPACA_API_KEY, ALPACA_SECRET)")
    return {"APCA-API-KEY-ID": creds.alpaca_key, "APCA-API-SECRET-KEY": creds.alpaca_secret,
            "User-Agent": "kaibot-alt/0.1"}


def _sighting_rows(state: dict[str, dict], source: str, current: dict[str, dict], compare: Sequence[str],
                   now: datetime) -> list[dict]:
    """Tonight's list against what the store knows of `source`: first_seen, changed, returned, gone."""
    prev = {k: r for k, r in state.items() if r["source"] == source}
    here = {k for k, r in prev.items() if r["kind"] != "gone"}
    if here and len(current) < SHRINK_LIMIT * len(here):
        raise ValueError(f"the list shrank from {len(here)} to {len(current)} entries: refused as a broken answer")
    out = []
    for key, cur in current.items():
        old = prev.get(key)
        if old is None:
            kind = "first_seen"
        elif old["kind"] == "gone":
            kind = "returned"
        elif any((old.get(c) or "") != (cur.get(c) or "") for c in compare):
            kind = "changed"
        else:
            continue
        out.append(_row(key, source, kind, baseline=not prev, available_ts=now, **cur))
    for key in sorted(here - set(current)):
        old = prev[key]
        keep = {c: old[c] for c in ("symbol", "company", "exchange", "status", "tradable", "attributes", "etf", "test")}
        out.append(_row(key, source, "gone", available_ts=now, **keep))
    return out


def collect_assets(root: Path, creds: Creds, now: datetime, **http_kw) -> int:
    doc = http.get_json(ASSETS_URL, source=BUDGET_ASSETS, headers=_alpaca_headers(creds), timeout=60,
                        params={"status": "active", "asset_class": "us_equity"}, max_bytes=64 * 1024 * 1024, **http_kw)
    current = {}
    for a in parse_assets(doc):
        ipo = "ipo" in a["attributes"].split(",")
        current[f"alpaca:{a['id']}"] = {**{k: v for k, v in a.items() if k != "id"}, "_ipo": ipo}
    if not current:
        raise ValueError("assets: no US equity in the answer")
    rows = _sighting_rows(_state_with_ipo(root), "alpaca_assets", current,
                          ("symbol", "company", "exchange", "status", "tradable", "_ipo"), now)
    for r in rows:
        r.pop("_ipo", None)
    n = _write(root, rows, now, lambda r: _et_date(now))
    log.info("listings alpaca: %d assets, %d rows written", len(current), n)
    return n


def _state_with_ipo(root: Path) -> dict[str, dict]:
    state = _state(root)
    for r in state.values():
        r["_ipo"] = "ipo" in (r.get("attributes") or "").split(",")
    return state


# ---------------------------------------------------------------- input 2: SEC EDGAR


def _sec_headers(creds: Creds) -> dict[str, str]:
    ua = creds.sec_user_agent
    if not ua or not _UA.match(ua) or "@" not in ua:
        raise ValueError("SEC_USER_AGENT must be set to 'Name contact@email' (SEC fair-access policy)")
    return {"User-Agent": ua, "Accept-Encoding": "identity"}


def _parse_utc(s: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(UTC) if t.tzinfo else None


def parse_submissions(doc: object, cik: str) -> dict:
    """What the watch needs from a company's submissions file: name, first ticker and exchange, SIC code, and
    the acceptance time of each recent filing."""
    if not isinstance(doc, dict) or str(doc.get("cik", "")).lstrip("0") != cik.lstrip("0"):
        raise ValueError(f"submissions for CIK {cik}: wrong or missing cik")
    recent = (doc.get("filings") or {}).get("recent")
    cols = ("accessionNumber", "acceptanceDateTime")
    if not isinstance(recent, dict) or not all(isinstance(recent.get(c), list) for c in cols):
        raise ValueError(f"submissions for CIK {cik}: unexpected schema")
    accepted = {a: t for a, at in zip(recent["accessionNumber"], recent["acceptanceDateTime"], strict=False)
                if isinstance(a, str) and _ACC.match(a) and (t := _parse_utc(at)) is not None}
    tickers = [t.upper() for t in doc.get("tickers") or [] if isinstance(t, str) and _ANY_SYMBOL.match(t)]
    exchanges = [str(x)[:20] for x in doc.get("exchanges") or [] if isinstance(x, str)]
    return {"company": str(doc.get("name") or "")[:300], "symbol": tickers[0] if tickers else "",
            "exchange": exchanges[0] if exchanges else "", "sic": str(doc.get("sic") or "")[:8], "accepted": accepted}


def fetch_cover(path: str, headers: dict[str, str], **http_kw) -> dict | None:
    """The top of a filing's text file, parsed as a prospectus cover. None when EDGAR has no such file."""
    if not _PATH.match(path):
        raise ValueError(f"bad filing path {path[:60]!r}")
    r = http.get(ARCHIVE_URL.format(path=path), source=BUDGET_SEC, timeout=60, ok_statuses=(404,),
                 headers={**headers, "Range": f"bytes=0-{COVER_BYTES - 1}"}, max_bytes=64 * 1024 * 1024, **http_kw)
    if r.status == 404:
        return None
    return parse_prospectus_cover(r.body[:COVER_BYTES * 2].decode("utf-8", errors="replace"))


def collect_edgar(root: Path, creds: Creds, now: datetime, day: date | None = None, **http_kw) -> int:
    h = _sec_headers(creds)
    state, meta = _state(root), _read_meta(root)
    ledger = meta["index_days"]
    first_run = not any(r["source"] == "edgar_index" for r in state.values()) and not ledger
    done = {date.fromisoformat(d) for d in ledger}
    days = [day] if day else index_days(now, done, FIRST_RUN_DAYS if first_run else LOOKBACK_DAYS)
    rows: dict[str, dict] = {}
    paths: dict[str, str] = {}
    missing, denied, got = [], 0, 0
    for d in days:
        url = INDEX_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, ymd=d.strftime("%Y%m%d"))
        try:
            r = http.get(url, source=BUDGET_SEC, headers=h, timeout=60, ok_statuses=(404,),
                         max_bytes=64 * 1024 * 1024, **http_kw)
        except http.HttpError as e:
            if e.status != 403:
                raise
            denied += 1
            missing.append(d)
            continue
        if r.status == 404:
            missing.append(d)
            continue
        avail = index_available_ts(d, now, r.last_modified())
        for x in master_index_rows(r):
            key = f"edgar:{x['cik']}:{x['accession']}"
            paths[key] = x["path"]
            rows[key] = _row(key, "edgar_index", "filing", company=x["company"], cik=x["cik"], form=x["form"],
                             accession=x["accession"], filed=x["filed"], available_ts=avail,
                             url=ARCHIVE_URL.format(path=x["path"]))
        ledger[d.isoformat()] = "ok"
        got += 1
    if denied and not got and not any(v == "ok" for v in ledger.values()):
        raise ValueError(f"SEC answered 403 for all {denied} daily indexes: check SEC_USER_AGENT in the env file")
    last_ok = max((date.fromisoformat(k) for k, v in ledger.items() if v == "ok"), default=None)
    for d in missing:  # a weekday without an index is a holiday once a later day's index exists
        if (last_ok is not None and d < last_ok) or now - index_floor(d) > timedelta(days=4):
            ledger[d.isoformat()] = "none"
    rows = {k: r for k, r in rows.items() if k not in state}
    extra = _enrich(state, rows, paths, meta, h, now, **http_kw)
    n = _write(root, [*rows.values(), *extra], now, lambda r: r["filed"] or _et_date(now))
    _write_meta(root, meta)
    log.info("listings edgar: %d index days read, %d without an index, %d rows written", got, len(missing), n)
    return n


def _enrich(state: dict[str, dict], new: dict[str, dict], paths: dict[str, str], meta: dict, h: dict[str, str],
            now: datetime, **http_kw) -> list[dict]:
    """For companies with a fresh 8-A12B or 424B4: acceptance times and the ticker from the submissions file, and
    the offer price from the top of a 424B4 of a company that also registered on an exchange. `new` rows are
    completed in place; stored rows that gain a value come back as new versions."""
    today = _et_date(now)
    stored = {k: dict(r) for k, r in state.items() if r["source"] == "edgar_index" and k not in new}
    filings = {**stored, **new}
    by_cik: dict[str, list[dict]] = {}
    for r in filings.values():
        by_cik.setdefault(r["cik"], []).append(r)
    cikmap = {r["cik"]: r for r in state.values() if r["source"] == "sec_submissions"}
    changed: dict[str, dict] = {}
    out: list[dict] = []
    asked = 0
    for cik in sorted(by_cik):
        fresh = [r for r in by_cik[cik] if r["form"] in (REGISTRATION, PROSPECTUS)
                 and (today - r["filed"]).days <= ENRICH_DAYS]
        no_ticker = not (cikmap.get(cik) or {}).get("symbol")
        want = any(r["accepted_ts"] is None for r in fresh) or (
            no_ticker and any((today - r["filed"]).days <= TICKER_RETRY_DAYS for r in fresh))
        if not want or asked >= MAX_ENRICH:
            continue
        asked += 1
        try:
            r = http.get(SUBMISSIONS_URL.format(cik=cik), source=BUDGET_SEC, headers=h, timeout=60,
                         ok_statuses=(404,), max_bytes=64 * 1024 * 1024, **http_kw)
            if r.status == 404:
                continue
            sub = parse_submissions(json.loads(r.body), cik)
        except (http.HttpError, ValueError) as e:
            log.warning("listings edgar: submissions of CIK %s not read: %s", cik, e)
            continue
        for f in by_cik[cik]:
            ts = sub["accepted"].get(f["accession"])
            if ts is not None and f["accepted_ts"] is None:
                f["accepted_ts"] = ts
                if f["key"] in stored:
                    changed[f["key"]] = f
        old = cikmap.get(cik)
        if old is None or (old.get("symbol") or "") != sub["symbol"] or (old.get("exchange") or "") != sub["exchange"]:
            out.append(_row(f"cikmap:{cik}", "sec_submissions", "ticker", cik=cik, company=sub["company"],
                            symbol=sub["symbol"], exchange=sub["exchange"], attributes=f"sic={sub['sic']}",
                            filed=today, available_ts=now, url=SUBMISSIONS_URL.format(cik=cik)))
    read = 0
    for cik in sorted(by_cik):
        reg = [r["filed"] for r in by_cik[cik] if r["form"] == REGISTRATION]
        for f in by_cik[cik]:
            path = paths.get(f["key"]) or (f["url"] or "").removeprefix(ARCHIVE_URL.format(path=""))
            if (f["form"] != PROSPECTUS or f["offer_price"] is not None or f["accession"] in meta["prospectus"]
                    or not _PATH.match(path) or read >= MAX_PROSPECTUS
                    or (today - f["filed"]).days > ENRICH_DAYS
                    or not any(abs((f["filed"] - d).days) <= 60 for d in reg)):
                continue
            read += 1
            try:
                cover = fetch_cover(path, h, **http_kw)
            except (http.HttpError, ValueError) as e:
                log.warning("listings edgar: prospectus %s not read: %s", f["accession"], e)
                continue
            meta["prospectus"][f["accession"]] = "none" if cover is None else "read"
            if cover:
                f.update(offer_price=cover["offer_price"], shares_offered=cover["shares_offered"],
                         pricing_date=cover["prospectus_date"])
                if f["key"] in stored and any(cover.values()):
                    changed[f["key"]] = f
    return [*changed.values(), *out]


# ---------------------------------------------------------------- input 3: Nasdaq Trader


def _nasdaq(url: str, **http_kw) -> http.Response:
    return http.get(url, source=BUDGET_NASDAQ, headers={"User-Agent": "kaibot-alt/0.1"}, timeout=60,
                    max_bytes=16 * 1024 * 1024, **http_kw)


def collect_symdir(root: Path, now: datetime, **http_kw) -> int:
    current: dict[str, dict] = {}
    for name, url in SYMDIR_URLS.items():
        rows, made = parse_symdir(_nasdaq(url, **http_kw).body.decode("utf-8", errors="replace"))
        if not rows:
            raise ValueError(f"{name}: no symbol in the file")
        for r in rows:
            current[f"symdir:{name}:{r['symbol']}"] = {**r, "exchange": name, "published_ts": made}
    rows = _sighting_rows(_state(root), "nasdaq_symdir", current, ("company", "etf", "test"), now)
    n = _write(root, rows, now, lambda r: _et_date(now))
    log.info("listings symbol directory: %d symbols, %d rows written", len(current), n)
    return n


def _notice_available(published: datetime | None, day: date) -> datetime:
    """A notice with a publication time is known from then; one with only a date, from the next midnight ET."""
    if published is not None:
        return published
    return datetime.combine(day + timedelta(days=1), time(0, 0), ET).astimezone(UTC)


def collect_notices(root: Path, now: datetime, notice_urls: Sequence[str] = (), **http_kw) -> int:
    state, meta = _state(root), _read_meta(root)
    items = parse_notices(_nasdaq(NOTICES_URL, **http_kw).body)
    in_feed = {it["url"] for it in items}
    items += [{"title": "", "url": u, "published": None, "description": ""} for u in dict.fromkeys(notice_urls)
              if u.startswith(NOTICE_HOSTS) and u not in in_feed]  # a page named by hand, older than the feed
    rows, pages = [], 0
    for it in items:
        key = f"notice:{it['url']}"
        if (state.get(key) or {}).get("symbol"):
            continue
        said = parse_notice_text(f"{it['title']}. {it['description']}")
        title, day = it["title"], _et_date(it["published"]) if it["published"] else None
        if not said["symbol"] and it["url"] not in meta["notice_pages"] and pages < MAX_NOTICE_PAGES:
            pages += 1
            try:
                page = strip_tags(_nasdaq(it["url"], **http_kw).body.decode("utf-8", errors="replace"))[:20_000]
            except http.HttpError as e:
                log.warning("listings notices: %s not read: %s", it["url"], e)
                page = None
            if page is not None:
                meta["notice_pages"][it["url"]] = now.isoformat(timespec="seconds")
                found = parse_notice_text(page)
                said = {**found, "kind": found["kind"] or said["kind"]}
                if not title:
                    title = next((ln for ln in page.splitlines() if _LISTING.search(ln)), "")[:300]
                if day is None and (m := _LONG_DATE.search(page)):
                    day = _long_date(m.group(1))
        if day is None or (key in state and not said["symbol"]):
            continue  # an undated page, or still nothing more to say about a stored notice
        company = notice_company(title) or re.split(r"\s+(?:Initial Public Offering|Direct Listing)", title)[0][:300]
        rows.append(_row(key, "nasdaq_notice", "notice", symbol=said["symbol"], company=company, filed=day,
                         first_trade_date=said["first_trade_date"], attributes=said["kind"], url=it["url"],
                         published_ts=it["published"], available_ts=_notice_available(it["published"], day)))
    n = _write(root, rows, now, lambda r: r["filed"])
    _write_meta(root, {**_read_meta(root), "notice_pages": meta["notice_pages"]})
    log.info("listings notices: %d listing notices in the feed, %d pages read, %d rows written", len(items), pages, n)
    return n


# ---------------------------------------------------------------- input 4: the first daily bar


def bar_candidates(state: dict[str, dict], now: datetime) -> dict[str, date]:
    """{symbol: anchor day} for the symbols whose first daily bar is worth one request: a symbol new at Alpaca
    that is not plainly a fund, unit, warrant, right or preferred share; a symbol a notice says has started
    trading; the ticker of a company that has priced (424B4). The anchor is the earliest sign that trading is
    near; bars are asked for from `BAR_WINDOW_DAYS` before it."""
    today = _et_date(now)
    have = {r["symbol"] for r in state.values() if r["source"] == "alpaca_bars"}
    cikmap = {r["cik"]: r["symbol"] for r in state.values() if r["source"] == "sec_submissions" and r["symbol"]}
    anchors: dict[str, list[date]] = {}
    future: set[str] = set()
    for r in state.values():
        if r["source"] == "alpaca_assets" and r["kind"] == "first_seen" and not r["baseline"]:
            if not _NOT_COMMON.search(r["company"] or "") and r["exchange"] not in UNLISTED:
                anchors.setdefault(r["symbol"], []).append(_et_date(r["available_ts"]))
        elif r["source"] == "nasdaq_notice" and r["symbol"] and r["first_trade_date"]:
            if r["first_trade_date"] > today:
                future.add(r["symbol"])
            else:
                anchors.setdefault(r["symbol"], []).append(r["first_trade_date"])
    priced = {r["cik"] for r in state.values() if r["source"] == "edgar_index" and r["form"] == PROSPECTUS
              and (today - r["filed"]).days <= BAR_WINDOW_DAYS}
    for r in state.values():
        if (r["source"] == "edgar_index" and r["form"] in (REGISTRATION, PROSPECTUS) and r["cik"] in priced
                and r["cik"] in cikmap and (today - r["filed"]).days <= BAR_WINDOW_DAYS):
            anchors.setdefault(cikmap[r["cik"]], []).append(r["filed"])
    return {s: min(v) for s, v in sorted(anchors.items()) if s not in have and s not in future and _SYMBOL.match(s)}


def collect_first_bars(root: Path, creds: Creds, now: datetime, **http_kw) -> int:
    headers = _alpaca_headers(creds)
    state, meta = _state(root), _read_meta(root)
    tries = meta["bar_tries"]
    rows, asked = [], 0
    for symbol, anchor in bar_candidates(state, now).items():
        if int(tries.get(symbol, 0)) >= MAX_BAR_TRIES or asked >= MAX_BAR_LOOKUPS:
            continue
        asked += 1
        tries[symbol] = int(tries.get(symbol, 0)) + 1
        start = anchor - timedelta(days=BAR_WINDOW_DAYS)
        params = {"timeframe": "1Day", "start": datetime(start.year, start.month, start.day, tzinfo=UTC).isoformat(),
                  "end": (now - SIP_DELAY).isoformat(), "limit": 10, "adjustment": "raw", "feed": "sip"}
        try:
            doc = http.get_json(BARS_URL.format(symbol=symbol), source=BUDGET_BARS, headers=headers, params=params,
                                timeout=60, ok_statuses=(), max_bytes=4 * 1024 * 1024, **http_kw)
        except http.HttpError as e:
            if e.status in (400, 404, 422):  # Alpaca's answers for a symbol it has no data for
                continue
            raise
        bars = doc.get("bars") if isinstance(doc, dict) else None
        t = _parse_utc(bars[0].get("t")) if isinstance(bars, list) and bars and isinstance(bars[0], dict) else None
        if t is None:
            continue
        first = _et_date(t)
        close = (nyse.close_utc(first) if nyse.is_session(first)
                 else datetime.combine(first, time(16, 0), ET).astimezone(UTC)) + BAR_DELAY
        if close > now:
            continue  # today's bar is not final yet: ask again tomorrow
        if first <= start + timedelta(days=BAR_OLD_DAYS):
            # it already traded weeks before the first sign of a listing: not new. No first date is known from
            # this, only that there is none in the window; Kaibot knows it from tonight
            rows.append(_row(f"bar:{symbol}", "alpaca_bars", "traded_before", symbol=symbol, filed=_et_date(now),
                             available_ts=now))
        else:
            rows.append(_row(f"bar:{symbol}", "alpaca_bars", "first_bar", symbol=symbol, first_trade_date=first,
                             filed=first, published_ts=close, available_ts=close))
    n = _write(root, rows, now, lambda r: r["filed"])
    _write_meta(root, {**_read_meta(root), "bar_tries": tries})
    log.info("listings first bars: %d symbols asked, %d rows written", asked, n)
    return n


# ---------------------------------------------------------------- the nightly run


def collect(root: Path, creds: Creds, *, now: datetime | None = None, day: date | None = None,
            notice_urls: Sequence[str] = (), **http_kw) -> int:
    """One night. Every input runs on its own: one that fails is logged and named in `InputsFailed`, raised at
    the end, after the others were stored. With `day`, only the EDGAR index of that day is read again."""
    now = now or datetime.now(UTC)
    steps = [("edgar_index", lambda: collect_edgar(root, creds, now, day, **http_kw))] if day else [
        ("alpaca_assets", lambda: collect_assets(root, creds, now, **http_kw)),
        ("edgar_index", lambda: collect_edgar(root, creds, now, **http_kw)),
        ("nasdaq_symdir", lambda: collect_symdir(root, now, **http_kw)),
        ("nasdaq_notices", lambda: collect_notices(root, now, notice_urls, **http_kw)),
        ("first_bars", lambda: collect_first_bars(root, creds, now, **http_kw))]
    written, failed = 0, {}
    for name, step in steps:
        try:
            written += step()
        except (http.HttpError, ValueError, KeyError, TypeError) as e:
            failed[name] = f"{type(e).__name__}: {str(e)[:200]}"
            log.error("listings %s: failed, the other inputs go on: %s", name, failed[name])
    ok = [name for name, _ in steps if name not in failed]
    beat = _row(f"run:{_et_date(now).isoformat()}", "collector", "run", available_ts=now, filed=_et_date(now),
                attributes=f"ok={','.join(ok)};failed={','.join(failed)}")
    _write(root, [beat], now, lambda r: r["filed"])  # which inputs ran tonight; not counted as an observation
    if failed:
        raise InputsFailed(failed, written)
    return written
