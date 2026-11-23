"""Source B of the dashboard's headline line: SEC EDGAR 8-K filings of the held and decided stocks (stakeholder and
Product, 23 Nov 2026). Off by default (KAIBOT_HEADLINE_EDGAR); written by kaibot.news.feed, shown by the dashboard.

Why a second source: the Alpaca/Benzinga headlines the engine already reads (Source A) may be shown to the single
owner only (Alpaca's individual data licence forbids redistribution). EDGAR filings are public records, so they are
the only headlines the dashboard may ever show to another user (licence `public`).

What it reads, and nothing more:
- `https://www.sec.gov/files/company_tickers.json` once per New York day (ticker -> CIK);
- `https://data.sec.gov/submissions/CIK##########.json` for a held or decided stock, at most once per symbol in
  MIN_GAP (10 minutes), at most MAX_PER_PASS symbols a pass, and only while the market is open (an NYSE session,
  09:30 to the close, early closes included). This JSON is the filing index (form, items, acceptance time); no
  filing body is ever fetched.
Every request goes through common.http under the shared `sec_edgar` budget (config/sources.yaml: 5 req/s; SEC's
fair-access limit is 10), with the User-Agent SEC asks for: a name and a contact address, from SEC_USER_AGENT
("Name contact@domain", the variable the SEC scripts already use). Without a valid one the source stays off.

What it writes: one line per 8-K / 8-K/A accepted in the last LOOKBACK, from the item codes only, e.g.
"AAPL filed results (8-K item 2.02)", "MSFT filed management change (8-K item 5.02)",
"NVDA filed material agreement and results (8-K items 1.01, 2.02)". The payload is untrusted JSON: rows that do not
parse are skipped, symbols and accession numbers are checked against fixed patterns, and the line is built from our
own words for each item code, never from text in the filing.

Pure parsing (`parse_submissions`, `render_line`) is tested on recorded fixtures; tests make no live call.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta

from kaibot.ingest import nyse_sessions as nyse

log = logging.getLogger("kaibot.ingest.edgar_8k_feed")
BUDGET = "sec_edgar"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
MIN_GAP = timedelta(minutes=10)  # per symbol
MAX_PER_PASS = 10
LOOKBACK = timedelta(days=3)
TIMEOUT_S = 10.0
MAX_BYTES = 16 * 1024 * 1024
FORMS = ("8-K", "8-K/A")
SOURCE = "sec_edgar"
LICENCE = "public"
MAX_LINE = 140
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_CIK = re.compile(r"^\d{1,10}$")
_ITEM = re.compile(r"^\d\.\d{2}$")
_UA = re.compile(r"^[^\r\n@]{2,80}\s+[^\s@]+@[^\s@]+\.[^\s@]+$")

# Our words for each 8-K item (Form 8-K general instructions). 9.01 (exhibits) is said only when it is the only item.
ITEM_WORDS: dict[str, str] = {
    "1.01": "material agreement",
    "1.02": "end of a material agreement",
    "1.03": "bankruptcy or receivership",
    "1.04": "mine safety report",
    "1.05": "cybersecurity incident",
    "2.01": "completed acquisition or sale",
    "2.02": "results",
    "2.03": "new debt",
    "2.04": "debt acceleration",
    "2.05": "restructuring costs",
    "2.06": "impairment",
    "3.01": "listing standards notice",
    "3.02": "unregistered share sale",
    "3.03": "change to shareholder rights",
    "4.01": "auditor change",
    "4.02": "restatement warning",
    "5.01": "change in control",
    "5.02": "management change",
    "5.03": "bylaws or fiscal year change",
    "5.04": "benefit plan trading halt",
    "5.05": "code of ethics change",
    "5.06": "shell company status change",
    "5.07": "shareholder vote results",
    "5.08": "director nominations",
    "6.01": "ABS information",
    "7.01": "Reg FD disclosure",
    "8.01": "other event",
    "9.01": "exhibits",
}
# Item codes that mean one of the dashboard's four tags by definition (not the headline tagger's guess).
ITEM_TAGS: dict[str, str] = {"5.02": "management_change"}


def valid_user_agent(ua: object) -> bool:
    """SEC's fair-access rule: a name and a contact e-mail address."""
    return isinstance(ua, str) and bool(_UA.match(ua.strip()))


def market_open(now: datetime) -> bool:
    d = now.astimezone(nyse.ET).date()
    return nyse.is_session(d) and nyse.open_utc(d) <= now.astimezone(UTC) < nyse.close_utc(d)


def split_items(items: object) -> list[str]:
    if not isinstance(items, str):
        return []
    out: list[str] = []
    for i in items.split(","):
        i = i.strip()
        if _ITEM.match(i) and i not in out:
            out.append(i)
    return out


def render_line(symbol: str, form: str, items: Iterable[str]) -> str:
    """One plain-text line from the item codes, at most MAX_LINE characters."""
    codes = list(items)
    said = [c for c in codes if c != "9.01"] or codes
    words = [ITEM_WORDS.get(c, f"item {c}") for c in said[:2]]
    what = " and ".join(words) if words else "a current report"
    if len(said) > 2:
        what += " and more"
    label = "8-K/A" if form == "8-K/A" else "8-K"
    if form == "8-K/A":
        what = f"an amended {what}"
    ref = f"{label} item {said[0]}" if len(said) == 1 else (f"{label} items {', '.join(said)}" if said else label)
    line = f"{symbol} filed {what} ({ref})"
    if len(line) > MAX_LINE:
        line = line[:MAX_LINE - 1].rstrip() + "…"
    return line


def _utc(v: object) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(UTC) if t.tzinfo else None


def filing_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"


def parse_submissions(doc: object, symbol: str, since: datetime) -> list[dict]:
    """The 8-K / 8-K/A filings accepted at or after `since` in one submissions JSON, newest first, as feed rows.
    Untrusted input: a document that is not the expected shape gives [], a malformed row is skipped."""
    if not _SYMBOL.match(symbol or ""):
        return []
    if not isinstance(doc, dict):
        return []
    cik = str(doc.get("cik") or "").strip()
    recent = (doc.get("filings") or {}).get("recent") if isinstance(doc.get("filings"), dict) else None
    cols = ("accessionNumber", "form", "acceptanceDateTime", "items")
    if not _CIK.match(cik) or not isinstance(recent, dict) or not all(isinstance(recent.get(c), list) for c in cols):
        return []
    n = len(recent["accessionNumber"])
    if any(len(recent[c]) != n for c in cols):
        return []
    out: list[dict] = []
    for acc, form, accepted, items in zip(*(recent[c] for c in cols), strict=True):
        if form not in FORMS or not (isinstance(acc, str) and _ACC.match(acc)):
            continue
        ts = _utc(accepted)
        if ts is None or ts < since:
            continue
        codes = split_items(items)
        out.append({"key": f"sec:{acc}", "symbols": [symbol], "headline": render_line(symbol, form, codes),
                    "source": SOURCE, "licence": LICENCE, "url": filing_url(cik, acc),
                    "tags": sorted({ITEM_TAGS[c] for c in codes if c in ITEM_TAGS}), "created_at": ts})
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def ticker_ciks(doc: object) -> dict[str, str]:
    """company_tickers.json -> {TICKER: 10-digit CIK} (BRK-B is listed as BRK.B, as the engine names it)."""
    out: dict[str, str] = {}
    for v in doc.values() if isinstance(doc, dict) else []:
        try:
            t = str(v["ticker"]).upper().replace("-", ".")
            c = str(v["cik_str"]).strip()
        except (KeyError, TypeError):
            continue
        if _SYMBOL.match(t) and _CIK.match(c):
            out.setdefault(t, c.zfill(10))
    return out


def sec_get(user_agent: str) -> Callable[[str], object]:
    """GET + JSON under the shared `sec_edgar` budget, with the declared User-Agent; gzip when offered."""
    from kaibot.common import http

    headers = {"User-Agent": user_agent.strip(), "Accept-Encoding": "gzip", "Accept": "application/json"}

    def get(url: str) -> object:
        r = http.get(url, source=BUDGET, headers=headers, timeout=TIMEOUT_S, max_bytes=MAX_BYTES, attempts=2)
        body = r.body
        if r.headers.get("content-encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return json.loads(body, strict=False)

    return get


class EdgarFilings:
    """The source: `rows(symbols)` -> feed rows of the symbols that are due. `get(url) -> parsed JSON`."""

    def __init__(self, get: Callable[[str], object], clock: Callable[[], datetime] | None = None,
                 min_gap: timedelta = MIN_GAP, max_per_pass: int = MAX_PER_PASS, lookback: timedelta = LOOKBACK):
        self.get = get
        self.clock = clock or (lambda: datetime.now(UTC))
        self.min_gap, self.max_per_pass, self.lookback = min_gap, max_per_pass, lookback
        self._asked: dict[str, datetime] = {}  # symbol -> when it was last asked (a failure counts: no retry storm)
        self._ciks: dict[str, str] = {}
        self._ciks_day: date | None = None
        self._ciks_tried: datetime | None = None
        self.requests = 0

    def due(self, symbols: Iterable[str], now: datetime | None = None) -> list[str]:
        now = now or self.clock()
        if not market_open(now):
            return []
        out = [s for s in sorted(set(symbols)) if _SYMBOL.match(s)
               and (s not in self._asked or now - self._asked[s] >= self.min_gap)]
        out.sort(key=lambda s: self._asked.get(s, datetime.min.replace(tzinfo=UTC)))
        return out[: self.max_per_pass]

    def _cik_map(self, now: datetime) -> dict[str, str]:
        day = now.astimezone(nyse.ET).date()
        if self._ciks_day == day:
            return self._ciks
        if self._ciks_tried is not None and now - self._ciks_tried < self.min_gap:
            return self._ciks  # yesterday's map (or none) until the next try
        self._ciks_tried = now
        self.requests += 1
        m = ticker_ciks(self.get(TICKERS_URL))
        if m:
            self._ciks, self._ciks_day = m, day
        return self._ciks

    def rows(self, symbols: Iterable[str]) -> list[dict]:
        now = self.clock()
        want = self.due(symbols, now)
        if not want:
            return []
        ciks = self._cik_map(now)
        out: list[dict] = []
        for s in want:
            self._asked[s] = now
            cik = ciks.get(s)
            if cik is None:
                continue  # an ETF or an unmapped ticker: nothing to ask
            self.requests += 1
            try:
                doc = self.get(SUBMISSIONS_URL.format(cik=cik))
            except Exception as e:  # one symbol's failure must not lose the others
                log.debug("edgar: %s unreadable (%s)", s, type(e).__name__)
                continue
            out.extend(parse_submissions(doc, s, now - self.lookback))
        return out


def from_env(env, clock: Callable[[], datetime] | None = None) -> EdgarFilings | None:
    """The source, or None when SEC_USER_AGENT is not 'Name contact@domain' (logged once by the caller)."""
    ua = (env or {}).get("SEC_USER_AGENT")
    if not valid_user_agent(ua):
        return None
    return EdgarFilings(sec_get(ua), clock=clock)


__all__ = ["EdgarFilings", "ITEM_WORDS", "parse_submissions", "render_line", "ticker_ciks", "from_env",
           "valid_user_agent", "market_open", "MIN_GAP"]
