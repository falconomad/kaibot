"""Spin-off share ratios from the parent's SEC 8-K filings (story #298). Shadow / research data only.

Why: the corporate-actions table lists a spin-off but carries no distribution value (#262), and #275 does not adopt
a record without a share ratio. The parent company states the ratio in its own 8-K filings ("one share of NewCo
common stock for every three shares of Parent common stock"), and EDGAR stamps every filing with its acceptance
time. So each row here is point-in-time by construction: `available_ts` is the acceptance time of the earliest
filing that states the ratio, never the ex-date.

Nothing in the live path reads this: no module under engine, risk, strategies, broker, features, replay or agents
may import it (tests/unit/test_spinoff_sec.py asserts that). Using the rows is a separate two-key story.

How a row is made, for one seed event (parent ticker, ex-date, new symbol; the seeds are the daily store's
`spin_off` rows or a CSV):
1. parent ticker -> CIK (today's www.sec.gov/files/company_tickers.json, or the `cik` column of the CSV);
2. the parent's 8-K filings accepted from `WINDOW_BEFORE_DAYS` before the ex-date to `WINDOW_AFTER_DAYS` after it,
   from data.sec.gov/submissions (items 1.01, 2.01, 7.01 or 8.01 only; at most `MAX_FILINGS`, nearest first);
3. per filing: the primary document, then (only if it states no ratio and the filing has exhibits) the folder's
   index.json and up to `MAX_EXHIBITS` EX-99 documents;
4. the ratio statements in the text, by fixed patterns (`statements`). No model reads the filing.
A row is written only when the filings state exactly one ratio for the new company. Several different ratios, or
none, is a miss with the reason (misses.csv): a wrong ratio is worse than no ratio.

Limits (see docs/ops/spinoff_ratios.md):
- The ex-date and the new symbol come from the seed, not from the filing.
- `value_per_parent_share` is filled only when a filing states a dollar value per parent share for the
  distribution in so many words. Most 8-Ks do not; the tax value is published later on Form 8937, which is not
  filed on EDGAR.
- Split-offs (exchange offers), Reverse Morris Trusts and fractions written as words ("one-half of a share") may
  be worded in ways the patterns do not read: they come out as misses, not as wrong rows.
- Every request runs under the shared `sec_edgar` budget (config/sources.yaml: 5 req/s) with SEC_USER_AGENT.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.earnings_sec import _parse_utc, cik10, older_files

log = logging.getLogger(__name__)
PKG = Path(__file__).with_name("spinoff")
CHECK_262 = PKG / "check_262.csv"
SOURCE = "sec_edgar_8k_spinoff"
BUDGET = "sec_edgar"
FORMS = {"8-K"}
ITEMS = {"1.01", "2.01", "7.01", "8.01"}  # where a distribution is announced or its completion reported
WINDOW_BEFORE_DAYS = 120
WINDOW_AFTER_DAYS = 7
MAX_FILINGS = 25  # per event
MAX_EXHIBITS = 3  # per filing
MAX_DOC_BYTES = 8 * 1024 * 1024
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
COLUMNS = ["parent", "new_symbol", "ex_date", "ratio", "new_shares", "parent_shares", "value_per_parent_share",
           "value_available_ts", "filing_url", "available_ts", "known_before_ex", "accession", "form", "items",
           "cik", "doc", "quote", "ratio_filings", "filings_read", "match", "alpaca_ratio", "source"]
_ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.(?:htm|html|txt)$")
_EX99 = re.compile(r"ex(?:hibit)?[-_]?99", re.I)


# ---------------------------------------------------------------- seeds


@dataclass(frozen=True)
class Event:
    parent: str  # the parent's ticker
    ex_date: date
    new_symbol: str = ""
    alpaca_ratio: float | None = None  # new shares per parent share in Alpaca's record, when it has both rates
    cik: str = ""  # optional: the parent's CIK when today's ticker table does not have the ticker

    @property
    def id(self) -> tuple[str, str, str]:
        return (self.parent, self.ex_date.isoformat(), self.new_symbol)


def events_from_actions(actions) -> list[Event]:
    """The daily store's spin-off rows (`DailyStore.actions()`, type `spin_off`; Alpaca's row is in `raw`)."""
    out: dict[tuple, Event] = {}
    if actions is None or not len(actions):
        return []
    for r in actions.to_dict("records"):
        if r.get("type") != "spin_off" or r.get("ex_date") is None:
            continue
        try:
            raw = json.loads(r.get("raw") or "{}")
        except (TypeError, ValueError):
            raw = {}
        parent = str(raw.get("source_symbol") or r.get("symbol") or str(r.get("key") or "").split("@")[0]).upper()
        ratio = None
        try:
            nr, sr = float(raw.get("new_rate")), float(raw.get("source_rate"))
            ratio = nr / sr if nr > 0 and sr > 0 else None
        except (TypeError, ValueError):
            pass
        try:
            ex = date.fromisoformat(str(r["ex_date"])[:10])
        except ValueError:
            continue
        if parent:
            e = Event(parent, ex, str(raw.get("new_symbol") or "").upper(), ratio)
            out.setdefault(e.id, e)
    return sorted(out.values(), key=lambda e: (e.ex_date, e.parent, e.new_symbol))


def read_events(path: Path) -> list[Event]:
    """CSV with the header parent,ex_date[,new_symbol][,cik][,...]; other columns are ignored."""
    out = []
    with Path(path).open(newline="") as fh:
        for r in csv.DictReader(fh):
            cik = (r.get("cik") or "").strip()
            out.append(Event(r["parent"].strip().upper(), date.fromisoformat(r["ex_date"].strip()),
                             (r.get("new_symbol") or "").strip().upper(), None, cik10(cik) if cik else ""))
    return out


# ---------------------------------------------------------------- the parent's 8-K filings (untrusted JSON)


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filing_date: date
    accepted_ts: datetime  # UTC
    items: str
    doc: str  # primary document

    def url(self, cik: str, doc: str | None = None) -> str:
        return ARCHIVE_URL.format(cik=int(cik), acc=self.accession.replace("-", ""), doc=doc or self.doc)


def parse_block(block: object, start: date) -> list[Filing]:
    """8-K filings since `start` from one columnar block of a submissions file. Malformed rows are skipped."""
    cols = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "items", "primaryDocument")
    if not isinstance(block, dict) or not all(isinstance(block.get(c), list) for c in cols):
        raise ValueError("submissions: unexpected schema")
    n = len(block["accessionNumber"])
    if any(len(block[c]) != n for c in cols):
        raise ValueError("submissions: ragged arrays")
    out = []
    for acc, fd, at, form, items, doc in zip(*(block[c] for c in cols), strict=True):
        if form not in FORMS:
            continue
        try:
            fdate = date.fromisoformat(fd)
        except (TypeError, ValueError):
            continue
        if fdate < start:
            continue
        ts = _parse_utc(at)
        if (not (isinstance(acc, str) and _ACC.match(acc)) or ts is None or not isinstance(doc, str)
                or not _DOC.match(doc)):
            log.warning("spinoff: skipping malformed 8-K row %r", str(acc)[:30])
            continue
        out.append(Filing(acc, form, fdate, ts, items if isinstance(items, str) else "", doc))
    return out


def filings_from_submissions(cik: str, doc: object, older: Mapping[str, object], start: date) -> list[Filing]:
    if not isinstance(doc, dict) or str(doc.get("cik", "")).lstrip("0") != cik.lstrip("0"):
        raise ValueError(f"submissions for CIK {cik}: wrong or missing cik")
    out = parse_block((doc.get("filings") or {}).get("recent"), start)
    for name in older_files(doc, start):
        if name not in older:
            raise ValueError(f"submissions for CIK {cik}: older file {name} not fetched")
        out += parse_block(older[name], start)
    return sorted({f.accession: f for f in out}.values(), key=lambda f: (f.accepted_ts, f.accession))


def ex_open(ex: date) -> datetime:
    """09:30 New York on the ex-date, in UTC: a filing accepted before it was known before the ex-date traded."""
    return datetime.combine(ex, time(9, 30), nyse.ET).astimezone(UTC)


def candidates(filings: Iterable[Filing], ex: date, before: int = WINDOW_BEFORE_DAYS, after: int = WINDOW_AFTER_DAYS,
               limit: int = MAX_FILINGS) -> list[Filing]:
    """The 8-Ks that may state the terms: accepted in the window around the ex-date, with one of `ITEMS`.
    Oldest first; when there are more than `limit`, the ones nearest the ex-date are kept."""
    lo, hi = ex_open(ex) - timedelta(days=before), ex_open(ex) + timedelta(days=after)
    hit = [f for f in filings if lo <= f.accepted_ts <= hi and ITEMS & {i.strip() for i in f.items.split(",")}]
    hit.sort(key=lambda f: abs((f.accepted_ts - ex_open(ex)).total_seconds()))
    return sorted(hit[:limit], key=lambda f: (f.accepted_ts, f.accession))


# ---------------------------------------------------------------- reading a document (untrusted HTML or text)

_WORDS = {w: i for i, w in enumerate(
    ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
     "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"], start=1)}
_NUM = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_PAREN = r"(?:\s*\(\s*[\d.]+\s*\))?"  # "one (1) share"
_SH = r"(?:whole\s+)?(?:ordinary\s+|common\s+)?shares?"
_NAME = r"(?:(?!\.\s+[A-Z])[^;:()]){2,140}?"  # a company and share-class name, never across a sentence end
_END = r"(?=\s+(?:held|owned|outstanding|that|they|you|as\s+of|on\s+the)\b|\s*[.,;)]|\s*$)"
# "one share of NewCo common stock for every three shares of Parent common stock"
_FORWARD = re.compile(
    rf"\b(?P<a>{_NUM}){_PAREN}\s+{_SH}\s+of\s+(?P<child>{_NAME})\s+for\s+(?:each|every)\s+"
    rf"(?:(?P<b>{_NUM}){_PAREN}\s+)?{_SH}\s+of\s+(?P<parent>{_NAME}){_END}", re.I)
# "for every three shares of Parent common stock held, shareholders received one share of NewCo common stock"
_REVERSED = re.compile(
    rf"\bfor\s+(?:each|every)\s+(?:(?P<b>{_NUM}){_PAREN}\s+)?{_SH}\s+of\s+(?P<parent>{_NAME})"
    rf"(?:,|\s+held|\s+owned)[^.;]{{0,200}}?\breceive[ds]?\s+(?P<a>{_NUM}){_PAREN}\s+{_SH}\s+of\s+"
    rf"(?P<child>{_NAME}){_END}", re.I)
_CONTEXT = re.compile(r"distribut|spin[\s-]?off|separation", re.I)
_CLASS = re.compile(r"\b(?:shares?\s+of|class\s+[a-z]|common|ordinary|stock|shares?|par\s+value.*)\b", re.I)
_TAG = re.compile(r"<(?:script|style)\b.*?</(?:script|style)>|<[^>]{0,2000}>", re.I | re.S)
_SPACE = re.compile(r"\s+")
_MONEY = r"\$\s?(?P<v>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"


def plain_text(raw: bytes | str) -> str:
    """Visible text of an EDGAR document: tags and scripts dropped, entities decoded, white space collapsed."""
    s = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    s = html.unescape(_TAG.sub(" ", s)).replace("\xa0", " ").replace("’", "'")
    return _SPACE.sub(" ", s).strip()


def _number(s: str | None) -> float:
    if s is None:
        return 1.0
    s = s.lower().strip()
    return float(_WORDS[s]) if s in _WORDS else float(s.replace(",", ""))


def company_of(name: str) -> str:
    """'GE HealthCare common stock' -> 'GE HEALTHCARE': the share-class words dropped, upper case."""
    return _SPACE.sub(" ", _CLASS.sub(" ", name)).strip(" ,.'").upper()


@dataclass(frozen=True)
class Statement:
    new_shares: float
    parent_shares: float
    child: str  # as written, e.g. "NewCo common stock"
    parent: str
    quote: str

    @property
    def ratio(self) -> float:
        return self.new_shares / self.parent_shares


def statements(text: str) -> list[Statement]:
    """Every "N shares of A for every M shares of B" in `text` that sits in a distribution context and names two
    different companies (a reverse split of one stock into itself does not qualify). Duplicates are dropped."""
    out: dict[tuple, Statement] = {}
    for rx in (_FORWARD, _REVERSED):
        for m in rx.finditer(text):
            lo, hi = max(0, m.start() - 500), min(len(text), m.end() + 500)
            if not _CONTEXT.search(text[lo:hi]):
                continue
            try:
                a, b = _number(m.group("a")), _number(m.group("b"))
            except ValueError:
                continue
            child, parent = m.group("child").strip(), m.group("parent").strip()
            if not (0 < a <= 100 and 0 < b <= 100) or not company_of(child) or company_of(child) == company_of(parent):
                continue
            s = Statement(a, b, child, parent, _SPACE.sub(" ", m.group(0))[:400])
            out.setdefault((round(s.ratio, 9), company_of(child)), s)
    return list(out.values())


def stated_value(text: str, parent: str) -> tuple[float, str] | None:
    """A dollar value of the distribution per share of the parent, only when one sentence says so: it must hold
    "value", "distribut..." and "$X per share of <parent>" (or "per <parent> share"), with the parent named as in
    the ratio statement. Returns (value, the sentence) or None. A value per share of the NEW company is not this."""
    who = company_of(parent)
    if not who:
        return None
    name = re.escape(who).replace(r"\ ", r"\s+")
    rx = re.compile(rf"{_MONEY}\s+(?:per|for\s+each)\s+(?:(?:whole\s+)?share\s+of\s+{name}\b|{name}\s+"
                    r"(?:common\s+|ordinary\s+)?share\b)", re.I)
    for sent in re.split(r"(?<=[.;])\s+(?=[A-Z])", text):
        if "value" not in sent.lower() or not re.search(r"distribut", sent, re.I):
            continue
        m = rx.search(sent)
        if m:
            v = float(m.group("v").replace(",", ""))
            if 0 < v < 100_000:
                return v, sent[:400]
    return None


def exhibits_from_index(doc: object, primary: str) -> list[str]:
    """EX-99 documents (press releases, information statements furnished with the 8-K) in a filing folder."""
    items = ((doc or {}).get("directory") or {}).get("item") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return []
    names = [i.get("name") for i in items if isinstance(i, dict) and isinstance(i.get("name"), str)]
    return sorted(n for n in names if _DOC.match(n) and _EX99.search(n) and n != primary)[:MAX_EXHIBITS]


# ---------------------------------------------------------------- fetching (resumable, budgeted)


@dataclass
class Extract:
    """What one filing states: the cached result of reading it (a few hundred bytes, not the documents)."""

    accession: str
    docs: list[str] = field(default_factory=list)  # documents read, in order
    found: list[dict] = field(default_factory=list)  # {"doc", **Statement, "value", "value_quote"}
    requests: int = 0
    error: str = ""


class Cache:
    """<data>/sec: submissions/CIK##########.json (the parent's 8-K list) and filings/<accession>.json (what each
    filing states). A file that exists is never fetched again unless `refresh` (submissions only: a filing never
    changes). Written atomically, so a stopped run resumes where it stopped. `get` None = offline."""

    def __init__(self, root: Path, get: Callable[[str], bytes] | None, start: date = date(2016, 1, 1)):
        self.root = Path(root)
        (self.root / "submissions").mkdir(parents=True, exist_ok=True)
        (self.root / "filings").mkdir(parents=True, exist_ok=True)
        self.get, self.start = get, start
        self.requests = 0
        self.errors: dict[str, str] = {}

    def _fetch(self, url: str) -> bytes:
        self.requests += 1
        return self.get(url)

    @staticmethod
    def _write(path: Path, obj: object) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj))
        tmp.replace(path)

    def filings(self, cik: str, refresh: bool = False) -> list[Filing] | None:
        p = self.root / "submissions" / f"CIK{cik}.json"
        if p.is_file() and not (refresh and self.get):
            return [Filing(r["accession"], r["form"], date.fromisoformat(r["filing_date"]),
                           datetime.fromisoformat(r["accepted_ts"]).astimezone(UTC), r["items"], r["doc"])
                    for r in json.loads(p.read_text())["filings"]]
        if self.get is None:
            return None
        try:
            doc = json.loads(self._fetch(SUBMISSIONS_URL.format(name=f"CIK{cik}.json")))
            older = {n: json.loads(self._fetch(SUBMISSIONS_URL.format(name=n))) for n in older_files(doc, self.start)}
            out = filings_from_submissions(cik, doc, older, self.start)
        except Exception as e:  # noqa: BLE001  (one bad company must not stop the run)
            self.errors[cik] = f"{type(e).__name__}: {str(e)[:160]}"
            log.warning("spinoff: CIK %s failed: %s", cik, self.errors[cik])
            return None
        self._write(p, {"cik": cik, "name": str(doc.get("name", ""))[:200],
                        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "filings": [{**asdict(f), "filing_date": f.filing_date.isoformat(),
                                     "accepted_ts": f.accepted_ts.isoformat()} for f in out]})
        return out

    def _read(self, f: Filing, cik: str, doc: str, ex: Extract) -> bool:
        text = plain_text(self._fetch(f.url(cik, doc)))
        ex.docs.append(doc)
        got = statements(text)
        for s in got:
            v = stated_value(text, s.parent)
            ex.found.append({"doc": doc, **asdict(s), "value": v[0] if v else None, "value_quote": v[1] if v else ""})
        return bool(got)

    def extract(self, f: Filing, cik: str) -> Extract | None:
        p = self.root / "filings" / f"{f.accession}.json"
        if p.is_file():
            return Extract(**json.loads(p.read_text()))
        if self.get is None:
            return None
        ex, before = Extract(f.accession), self.requests
        try:
            if not self._read(f, cik, f.doc, ex) and "9.01" in f.items:
                idx = json.loads(self._fetch(f.url(cik, "index.json")))
                for name in exhibits_from_index(idx, f.doc):
                    self._read(f, cik, name, ex)
        except Exception as e:  # noqa: BLE001  (one unreadable filing must not stop the run; it is retried next run)
            self.errors[f.accession] = f"{type(e).__name__}: {str(e)[:160]}"
            log.warning("spinoff: filing %s failed: %s", f.accession, self.errors[f.accession])
            return None
        ex.requests = self.requests - before
        self._write(p, asdict(ex))
        return ex


def sec_getter(user_agent: str) -> Callable[[str], bytes]:
    """GET under the shared `sec_edgar` budget with the declared User-Agent (the same helper as the earnings
    collector), with this collector's cap on a document's size."""
    import gzip

    from kaibot.common import http

    if "@" not in user_agent:
        raise SystemExit("SEC_USER_AGENT must be 'Name email@domain' (SEC fair-access rule)")
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip"}

    def get(url: str) -> bytes:
        big = url.startswith("https://data.sec.gov/")  # a submissions file of a large filer is tens of MB
        r = http.get(url, source=BUDGET, headers=headers, timeout=60,
                     max_bytes=256 * 1024 * 1024 if big else MAX_DOC_BYTES)
        body = r.body
        if r.headers.get("content-encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return body

    return get


def ticker_table(company_tickers: object) -> dict[str, tuple[str, str]]:
    """company_tickers.json -> {ticker (BRK.B): (CIK, company title)}."""
    out: dict[str, tuple[str, str]] = {}
    vals = company_tickers.values() if isinstance(company_tickers, dict) else []
    for v in vals:
        try:
            out.setdefault(str(v["ticker"]).upper().replace("-", "."), (cik10(v["cik_str"]), str(v.get("title", ""))))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


# ---------------------------------------------------------------- one row per spin-off


def _names_child(child_text: str, title: str, words: int) -> bool:
    want = company_of(title).split()[:words]
    return bool(want) and company_of(child_text).split()[:len(want)] == want


def choose(found: Sequence[tuple[Filing, dict]], child_title: str = "") -> tuple[list[tuple[Filing, dict]], str, str]:
    """The statements that give this spin-off's ratio: (statements, how, why not).

    `found` is every statement of every candidate filing, oldest filing first. With one ratio in all of them, that
    is it ("only_ratio"). With several (a parent that spins off two companies on one day), only the statements whose
    new company starts with the first word, then the first two words, of the new symbol's EDGAR name count
    ("child_name"). Still several, or no name to go by: no row."""
    if not found:
        return [], "", "no ratio statement in the filings read"
    ratios = {round(s["new_shares"] / s["parent_shares"], 9) for _, s in found}
    if len(ratios) == 1:
        return list(found), "only_ratio", ""
    for words in (1, 2):
        hit = [(f, s) for f, s in found if _names_child(s["child"], child_title, words)]
        if hit and len({round(s["new_shares"] / s["parent_shares"], 9) for _, s in hit}) == 1:
            return hit, "child_name", ""
    seen = sorted({f"{s['new_shares']:g} per {s['parent_shares']:g} ({company_of(s['child'])})" for _, s in found})
    return [], "", "several ratios stated: " + "; ".join(seen)[:300]


def collect(events: Sequence[Event], cache: Cache, tickers: Mapping[str, tuple[str, str]],
            refresh: bool = False) -> tuple[list[dict], list[dict]]:
    """(rows, misses): one row per event whose filings state exactly one ratio, one miss with the reason otherwise."""
    rows, misses = [], []
    for e in events:
        cik = e.cik or tickers.get(e.parent, ("", ""))[0]
        base = {"parent": e.parent, "new_symbol": e.new_symbol, "ex_date": e.ex_date.isoformat()}
        if not cik:
            misses.append({**base, "reason": "parent ticker not in today's SEC ticker table (give a cik)"})
            continue
        fl = cache.filings(cik, refresh)
        if fl is None:
            misses.append({**base, "reason": f"no filing list for CIK {cik}: {cache.errors.get(cik, 'not cached')}"})
            continue
        cands = candidates(fl, e.ex_date)
        found, read = [], 0
        for f in cands:
            ex = cache.extract(f, cik)
            if ex is None:
                continue
            read += 1
            found += [(f, s) for s in ex.found]
        hit, how, why = choose(found, tickers.get(e.new_symbol, ("", ""))[1] if e.new_symbol else "")
        if not hit:
            misses.append({**base, "reason": f"{why} ({read} of {len(cands)} candidate 8-Ks read)"})
            continue
        f, s = hit[0]  # oldest filing first: the first time the ratio was public
        val = next(((g, t) for g, t in hit if t.get("value") is not None), None)
        rows.append({
            **base, "ex_date": e.ex_date, "ratio": s["new_shares"] / s["parent_shares"],
            "new_shares": s["new_shares"], "parent_shares": s["parent_shares"],
            "value_per_parent_share": val[1]["value"] if val else math.nan,
            "value_available_ts": val[0].accepted_ts if val else None,
            "filing_url": f.url(cik, s["doc"]), "available_ts": f.accepted_ts,
            "known_before_ex": f.accepted_ts < ex_open(e.ex_date), "accession": f.accession, "form": f.form,
            "items": f.items, "cik": cik, "doc": s["doc"], "quote": s["quote"],
            "ratio_filings": len({g.accession for g, _ in hit}), "filings_read": read, "match": how,
            "alpaca_ratio": e.alpaca_ratio if e.alpaca_ratio is not None else math.nan, "source": SOURCE})
    return rows, misses


def table(rows: Sequence[dict]):
    """The research table (pandas DataFrame, columns COLUMNS), one row per spin-off, UTC timestamps."""
    import pandas as pd

    df = pd.DataFrame(list(rows), columns=COLUMNS)
    for c in ("available_ts", "value_available_ts"):
        df[c] = pd.to_datetime(df[c], utc=True)
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
    for c in ("ratio", "new_shares", "parent_shares", "value_per_parent_share", "alpaca_ratio"):
        df[c] = df[c].astype("float64")
    return df.sort_values(["ex_date", "parent", "new_symbol"], ignore_index=True)


def known_at(df, as_of: datetime):
    """Point in time: the rows a reader at `as_of` could have had (`available_ts <= as_of`). A value stated only in
    a later filing is blanked until that filing was accepted."""
    import pandas as pd

    ts = pd.Timestamp(as_of)
    if ts.tzinfo is None:
        raise ValueError("as_of needs a time zone")
    out = df[df["available_ts"] <= ts].copy()
    late = ~(out["value_available_ts"] <= ts)
    out.loc[late, "value_per_parent_share"] = math.nan
    out.loc[late, "value_available_ts"] = pd.NaT
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- the check against Alpaca (#262)


def implied_factor(parent_close: float, child_close: float, ratio: float) -> float:
    """The close factor a spin-off of `ratio` new shares per parent share implies, as #275 defines it: P / (P + V),
    P the parent's close on the ex-date and V = ratio x the new company's close that day."""
    if not (parent_close > 0 and child_close > 0 and ratio > 0):
        return math.nan
    return parent_close / (parent_close + ratio * child_close)


def compare(rows: Sequence[dict], reference: Sequence[Mapping], closes: Callable[[str, date], float] | None = None,
            tol: float = 0.02) -> list[dict]:
    """One line per reference spin-off (parent, ex_date, alpaca_close_factor): found or not, the SEC ratio, Alpaca's
    own ratio when its record has one, and, when `closes(symbol, day)` can price both companies on the ex-date, the
    factor the SEC ratio implies next to Alpaca's close factor (`agrees` = within `tol`)."""
    by = {(r["parent"], str(r["ex_date"])[:10]): r for r in rows}
    out = []
    for ref in reference:
        key = (str(ref["parent"]).upper(), str(ref["ex_date"])[:10])
        r = by.get(key)
        try:
            af = float(ref.get("alpaca_close_factor") or "nan")
        except ValueError:
            af = math.nan
        line = {"parent": key[0], "ex_date": key[1], "found": r is not None, "sec_ratio": math.nan,
                "alpaca_ratio": math.nan, "ratio_agrees": None, "alpaca_close_factor": af,
                "sec_implied_factor": math.nan, "factor_diff": math.nan, "agrees": None, "available_ts": "",
                "known_before_ex": None, "filing_url": ""}
        if r is not None:
            ar = r.get("alpaca_ratio", math.nan)
            line.update(sec_ratio=r["ratio"], alpaca_ratio=ar, available_ts=str(r["available_ts"]),
                        known_before_ex=bool(r["known_before_ex"]), filing_url=r["filing_url"])
            if ar == ar and ar is not None:
                line["ratio_agrees"] = bool(abs(r["ratio"] - ar) <= 1e-6 * max(1.0, ar))
            if closes is not None and r.get("new_symbol"):
                ex = date.fromisoformat(key[1])
                imp = implied_factor(closes(key[0], ex), closes(r["new_symbol"], ex), r["ratio"])
                line["sec_implied_factor"] = imp
                if imp == imp and af == af:
                    line["factor_diff"] = imp - af
                    line["agrees"] = bool(abs(imp - af) <= tol)
        out.append(line)
    return out
