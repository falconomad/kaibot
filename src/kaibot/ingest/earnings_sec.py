"""Earnings announcement times from SEC EDGAR 8-K filings with Item 2.02 (story S85, #125). Research data only.

Item 2.02 ("Results of Operations and Financial Condition") is the 8-K item under which a company furnishes its
earnings release. EDGAR stamps every filing with its acceptance time, so the announcement time is known to the
second and is point-in-time by construction: nobody could read the filing before EDGAR accepted it.

Source (checked 12 Nov 2026 on sec.gov "EDGAR Application Programming Interfaces" and "Accessing EDGAR Data"):
- https://data.sec.gov/submissions/CIK##########.json per company: "at least one year's of filing or to 1,000
  (whichever is more) of the most recent filings in a compact columnar data array" (`filings.recent`: parallel
  arrays accessionNumber, filingDate, acceptanceDateTime, form, items, ...). Older filings sit in the files listed
  under `filings.files` (name, filingFrom, filingTo), same columns. acceptanceDateTime is UTC ("...Z"; checked
  against live EDGAR for the Form 4 collector, 10 Nov 2026; `tz_check` re-checks it on every build).
- https://www.sec.gov/files/company_tickers.json: today's ticker -> CIK. Companies that no longer trade are not in
  it; for them the CIK comes from the committed overrides (`earnings/cik_overrides.csv`) or from the issuer names in
  IVV's holdings (`sp500/ivv_names.csv`) matched against https://www.sec.gov/Archives/edgar/cik-lookup-data.txt
  ("current list of all CIKs matched with entity name"), checked by the company's own filings.
- Fair access: "Current max request rate: 10 requests/second", with a declared User-Agent. Every request here goes
  through the shared `sec_edgar` budget (config/sources.yaml: 5 req/s) with SEC_USER_AGENT.

What a row is NOT: Item 2.02 also covers pre-announcements and some restatements; a few companies put the release
under Item 7.01 or 8.01 only. Rows are kept as filed; `report` marks the first 2.02 filing after a gap of at least
`REPORT_GAP_DAYS` (the quarterly report), the others are follow-ups. Amendments (8-K/A) are not used.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.sp500_pit import IVV_NAMES, Membership, _rows, norm_name

log = logging.getLogger(__name__)
PKG = Path(__file__).with_name("earnings")
OVERRIDES = PKG / "cik_overrides.csv"
START = date(2016, 1, 1)
ITEM = "2.02"
FORMS = {"8-K"}
REPORT_GAP_DAYS = 30
SOURCE = "sec_edgar_8k_item_2.02"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
BUDGET = "sec_edgar"
MAX_CANDIDATES = 4  # name matches checked per unresolved company (one request each)
COLUMNS = ["symbol", "company", "cik", "accepted_ts", "session", "last_close_before", "filing_date", "form",
           "items", "accession", "report", "source"]
_CIK = re.compile(r"^\d{1,10}$")
_ACC = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_FILE = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")
_PERIODIC = {"10-K", "10-Q", "10-K405", "20-F", "40-F"}


def cik10(c: object) -> str:
    s = str(c).strip()
    if not _CIK.match(s):
        raise ValueError(f"bad CIK {s[:20]!r}")
    return s.zfill(10)


# ---------------------------------------------------------------- parsing (input is untrusted JSON)


@dataclass(frozen=True)
class Filing:
    accession: str
    form: str
    filing_date: date
    accepted_ts: datetime  # UTC
    items: str


def _parse_utc(s: object) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(UTC) if t.tzinfo else None


def parse_block(block: object, start: date = START) -> tuple[list[Filing], list[date]]:
    """(8-K Item 2.02 filings, filing dates of periodic reports) since `start` from one columnar block
    (`filings.recent` or an older-filings file). Malformed rows are skipped."""
    cols = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "items")
    if not isinstance(block, dict) or not all(isinstance(block.get(c), list) for c in cols):
        raise ValueError("submissions: unexpected schema")
    n = len(block["accessionNumber"])
    if any(len(block[c]) != n for c in cols):
        raise ValueError("submissions: ragged arrays")
    out, periodic = [], []
    for acc, fd, at, form, items in zip(*(block[c] for c in cols), strict=True):
        try:
            fdate = date.fromisoformat(fd)
        except (TypeError, ValueError):
            continue
        if fdate < start:
            continue
        if form in _PERIODIC:
            periodic.append(fdate)
        if form not in FORMS or not isinstance(items, str):
            continue
        if ITEM not in {i.strip() for i in items.split(",")}:
            continue
        ts = _parse_utc(at)
        if not (isinstance(acc, str) and _ACC.match(acc)) or ts is None:
            log.warning("earnings: skipping malformed 8-K row %r", str(acc)[:30])
            continue
        out.append(Filing(acc, form, fdate, ts, items))
    return out, periodic


def older_files(doc: object, start: date = START) -> list[str]:
    """Names of the older-filings files that reach `start` or later."""
    files = ((doc.get("filings") or {}).get("files") or []) if isinstance(doc, dict) else []
    out = []
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not _FILE.match(f["name"]):
            continue
        try:
            to = date.fromisoformat(str(f.get("filingTo")))
        except ValueError:
            to = date.max
        if to >= start:
            out.append(f["name"])
    return out


@dataclass
class Company:
    """One CIK's cached extract: what this story needs from its submissions, not the whole JSON."""

    cik: str
    name: str = ""
    tickers: list[str] = field(default_factory=list)
    former_names: list[str] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    periodic: list[date] = field(default_factory=list)
    fetched_at: str = ""
    requests: int = 0

    def to_json(self) -> dict:
        return {"cik": self.cik, "name": self.name, "tickers": self.tickers, "former_names": self.former_names,
                "fetched_at": self.fetched_at, "requests": self.requests,
                "periodic": sorted({d.isoformat() for d in self.periodic}),
                "filings": [{"accession": f.accession, "form": f.form, "filing_date": f.filing_date.isoformat(),
                             "accepted_ts": f.accepted_ts.isoformat(), "items": f.items}
                            for f in sorted(self.filings, key=lambda f: f.accepted_ts)]}

    @classmethod
    def from_json(cls, d: dict) -> Company:
        return cls(cik=d["cik"], name=d.get("name", ""), tickers=list(d.get("tickers", [])),
                   former_names=list(d.get("former_names", [])), fetched_at=d.get("fetched_at", ""),
                   requests=int(d.get("requests", 0)),
                   periodic=[date.fromisoformat(x) for x in d.get("periodic", [])],
                   filings=[Filing(f["accession"], f["form"], date.fromisoformat(f["filing_date"]),
                                   datetime.fromisoformat(f["accepted_ts"]).astimezone(UTC), f["items"])
                            for f in d.get("filings", [])])

    def merge(self, other: Company) -> None:
        seen = {f.accession: f for f in self.filings}
        seen.update({f.accession: f for f in other.filings})
        self.filings = sorted(seen.values(), key=lambda f: f.accepted_ts)
        self.periodic = sorted(set(self.periodic) | set(other.periodic))
        self.name, self.tickers, self.former_names = other.name, other.tickers, other.former_names
        self.fetched_at, self.requests = other.fetched_at, self.requests + other.requests


def company_from_submissions(cik: str, doc: object, older: Mapping[str, object], start: date = START) -> Company:
    if not isinstance(doc, dict) or str(doc.get("cik", "")).lstrip("0") != cik.lstrip("0"):
        raise ValueError(f"submissions for CIK {cik}: wrong or missing cik")
    filings, periodic = parse_block((doc.get("filings") or {}).get("recent"), start)
    for name in older_files(doc, start):
        if name not in older:
            raise ValueError(f"submissions for CIK {cik}: older file {name} not fetched")
        f, p = parse_block(older[name], start)
        filings += f
        periodic += p
    uniq = {f.accession: f for f in filings}
    former = [str(x.get("name", "")) for x in doc.get("formerNames") or [] if isinstance(x, dict)]
    return Company(cik=cik, name=str(doc.get("name", ""))[:200],
                   tickers=[str(t)[:12] for t in doc.get("tickers") or []][:10], former_names=former[:20],
                   filings=sorted(uniq.values(), key=lambda f: f.accepted_ts), periodic=sorted(set(periodic)),
                   fetched_at=datetime.now(UTC).isoformat(timespec="seconds"), requests=1 + len(older))


# ---------------------------------------------------------------- fetching (resumable, budgeted)


def sec_getter(user_agent: str) -> Callable[[str], bytes]:
    """GET under the shared `sec_edgar` budget with the declared User-Agent; gzip transfer when offered."""
    from kaibot.common import http

    if "@" not in user_agent:
        raise SystemExit("SEC_USER_AGENT must be 'Name email@domain' (SEC fair-access rule)")
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip"}

    def get(url: str) -> bytes:
        r = http.get(url, source=BUDGET, headers=headers, timeout=60, max_bytes=256 * 1024 * 1024)
        body = r.body
        if r.headers.get("content-encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return body

    return get


class Cache:
    """data/earnings/sec/CIK##########.json: one small extract per CIK. A CIK with a file is never fetched again
    unless `refresh`; files are written atomically, so a stopped run resumes where it stopped."""

    def __init__(self, root: Path, get: Callable[[str], bytes] | None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.get = get
        self.requests = 0
        self.errors: dict[str, str] = {}

    def path(self, cik: str) -> Path:
        return self.root / f"CIK{cik}.json"

    def load(self, cik: str) -> Company | None:
        p = self.path(cik)
        return Company.from_json(json.loads(p.read_text())) if p.is_file() else None

    def company(self, cik: str, refresh: bool = False) -> Company | None:
        cached = self.load(cik)
        if cached is not None and not refresh:
            return cached
        if self.get is None:
            return cached
        try:
            doc = json.loads(self.get(SUBMISSIONS_URL.format(name=f"CIK{cik}.json")))
            self.requests += 1
            # a refresh only needs `recent` (it reaches back at least a year); a first fetch needs every file
            names = [] if cached is not None else older_files(doc)
            older = {}
            for n in names:
                older[n] = json.loads(self.get(SUBMISSIONS_URL.format(name=n)))
                self.requests += 1
            if cached is not None:
                doc = {**doc, "filings": {"recent": (doc.get("filings") or {}).get("recent"), "files": []}}
            fresh = company_from_submissions(cik, doc, older)
        except Exception as e:  # noqa: BLE001  (one bad company must not stop a 700-company run)
            self.errors[cik] = f"{type(e).__name__}: {str(e)[:160]}"
            log.warning("earnings: CIK %s failed: %s", cik, self.errors[cik])
            return cached
        if cached is not None:
            cached.merge(fresh)
            fresh = cached
        tmp = self.path(cik).with_suffix(".tmp")
        tmp.write_text(json.dumps(fresh.to_json()))
        tmp.replace(self.path(cik))
        return fresh


# ---------------------------------------------------------------- ticker -> CIK


def ticker_ciks(company_tickers: object) -> dict[str, str]:
    """company_tickers.json -> {ticker as in changes.csv (BRK.B): CIK}."""
    out: dict[str, str] = {}
    vals = company_tickers.values() if isinstance(company_tickers, dict) else []
    for v in vals:
        try:
            out.setdefault(str(v["ticker"]).upper().replace("-", "."), cik10(v["cik_str"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def read_overrides(path: Path = OVERRIDES) -> dict[str, tuple[str, str]]:
    """company -> (CIK, note). A blank CIK means "checked: no EDGAR filer" and stops the automatic search."""
    out = {}
    for r in _rows(path):
        out[r["company"]] = (cik10(r["cik"]) if r["cik"].strip() else "", r.get("note", ""))
    return out


def parse_cik_lookup(text: str) -> dict[str, list[str]]:
    """cik-lookup-data.txt ("NAME:0000123456:" per line) -> {normalised name: [CIKs]}."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = line.rstrip().rsplit(":", 2)
        if len(parts) != 3 or not _CIK.match(parts[1]):
            continue
        n = norm_name(parts[0])
        if n:
            lst = out.setdefault(n, [])
            if parts[1].zfill(10) not in lst:
                lst.append(parts[1].zfill(10))
    return out


def company_names(m: Membership, table: Path = IVV_NAMES) -> dict[str, set[str]]:
    """company -> normalised issuer names from IVV's holdings (by any of its point-in-time tickers)."""
    by_ticker: dict[str, set[str]] = {}
    for r in _rows(table):
        by_ticker.setdefault(r["ticker"], set()).add(norm_name(r["ivv_name"]))
    out: dict[str, set[str]] = {}
    for c, sps in m.spells.items():
        ts = {c.split("#")[0]} | {t for sp in sps for t in sp.tickers}
        out[c] = {n for t in ts for n in by_ticker.get(t, ()) if n}
    return out


@dataclass(frozen=True)
class Mapping_:
    company: str
    cik: str  # "" = miss
    how: str  # override | ticker | ticker_former | name | miss
    verified: bool
    note: str = ""


def _in_spells(m: Membership, company: str, co: Company | None) -> int:
    """Filings (2.02 or periodic reports) of `co` inside the company's membership spells: evidence the CIK is it."""
    if co is None:
        return 0
    days = [f.accepted_ts.date() for f in co.filings] + list(co.periodic)
    return sum(1 for d in days for sp in m.spells[company] if sp.covers(d))


def _name_ok(names: set[str], co: Company | None) -> bool:
    """The CIK's EDGAR names share a first word with the company's IVV issuer names (True when there are none
    to compare): guards against a ticker that was later given to another, older filer."""
    if co is None:
        return False
    if not names:
        return True
    firsts = {n.split()[0] for n in names if n}
    return any((norm_name(x).split() or [""])[0] in firsts for x in [co.name, *co.former_names])


def resolve(m: Membership, tickers: Mapping[str, str], overrides: Mapping[str, tuple[str, str]],
            fetch: Callable[[str], Company | None], names: Mapping[str, set[str]] | None = None,
            lookup: Callable[[], Mapping[str, list[str]]] | None = None,
            companies: Iterable[str] | None = None) -> list[Mapping_]:
    """Map every company of the membership log to a CIK.

    Order: committed override; today's ticker table under the company id (current members, renamed companies);
    today's table under an older point-in-time ticker (kept only if the CIK filed during a membership spell, so a
    reused ticker is not taken for the old company); IVV issuer names matched in EDGAR's name list (each candidate
    fetched, kept if exactly one filed during a spell). Everything else is a miss, listed with the reason."""
    out = []
    names = names or {}
    todo = sorted(companies if companies is not None else m.spells)
    pending: list[str] = []
    for c in todo:
        base = c.split("#")[0]
        if c in overrides:
            cik, note = overrides[c]
            if cik:
                out.append(Mapping_(c, cik, "override", _in_spells(m, c, fetch(cik)) > 0, note))
            else:
                out.append(Mapping_(c, "", "miss", False, f"override: {note}"))
            continue
        if "#" not in c and base in tickers:
            cik = tickers[base]
            co = fetch(cik)
            ok = _in_spells(m, c, co) > 0 and _name_ok(names.get(c, set()), co)
            if ok or m.spells[c][-1].end is None:
                out.append(Mapping_(c, cik, "ticker", ok, "" if ok else "current member, no filing seen in spell"))
                continue
        hit = None
        for t in reversed([t for sp in m.spells[c] for t in sp.tickers]):
            if t in tickers and t != base:
                cik = tickers[t]
                co = fetch(cik)
                if _in_spells(m, c, co) > 0 and _name_ok(names.get(c, set()), co):
                    hit = Mapping_(c, cik, "ticker_former", True, f"via {t}")
                    break
        if hit:
            out.append(hit)
        else:
            pending.append(c)
    if pending and names and lookup is not None:
        table = lookup()
        for c in pending:
            cands = sorted({k for n in names.get(c, ()) for k in table.get(n, ())})
            if not cands:
                why = "no IVV issuer name" if not names.get(c) else "IVV name not in EDGAR's name list"
                out.append(Mapping_(c, "", "miss", False, why))
                continue
            if len(cands) > MAX_CANDIDATES:
                out.append(Mapping_(c, "", "miss", False, f"{len(cands)} EDGAR names match: " + "|".join(cands[:8])))
                continue
            good = [k for k in cands if _in_spells(m, c, fetch(k)) > 0]
            if len(good) == 1:
                out.append(Mapping_(c, good[0], "name", True, "IVV issuer name"))
            else:
                out.append(Mapping_(c, "", "miss", False,
                                    f"{len(good)} of {len(cands)} name matches filed in the spell: " + "|".join(cands)))
    else:
        out += [Mapping_(c, "", "miss", False, "not in today's ticker table") for c in pending]
    return sorted(out, key=lambda x: x.company)


def write_mapping(rows: Sequence[Mapping_], path: Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["company", "cik", "how", "verified", "note"])
        for r in rows:
            w.writerow([r.company, r.cik, r.how, int(r.verified), r.note])


# ---------------------------------------------------------------- the announcements table


def ticker_on(m: Membership, company: str, d: date) -> str:
    """The point-in-time index ticker of `company` on `d` (nearest spell when outside every spell)."""
    sps = m.spells[company]
    for sp in sps:
        if sp.covers(d):
            # a rename inside a spell: the ticker in force on d
            for t in reversed(sp.tickers):
                if m.company_of(t, d) == company and t in m.members_on(d):
                    return t
            return sp.tickers[-1]
    before = [sp for sp in sps if sp.start <= d]
    return (before[-1].tickers[-1] if before else sps[0].tickers[0])


def build(m: Membership, mapping: Sequence[Mapping_], load: Callable[[str], Company | None]):
    """The announcements table (pandas DataFrame, columns COLUMNS), one row per 8-K Item 2.02 filing since 2016."""
    import pandas as pd

    rows = []
    for mp in mapping:
        if not mp.cik:
            continue
        co = load(mp.cik)
        if co is None:
            continue
        last = None
        for f in co.filings:
            d = f.accepted_ts.astimezone(nyse.ET).date()
            report = last is None or (f.accepted_ts - last) >= timedelta(days=REPORT_GAP_DAYS)
            last = f.accepted_ts if report else last
            rows.append({"symbol": ticker_on(m, mp.company, d), "company": mp.company, "cik": mp.cik,
                         "accepted_ts": f.accepted_ts, "session": nyse.first_session_opening_after(f.accepted_ts),
                         "last_close_before": nyse.last_session_closed_by(f.accepted_ts),
                         "filing_date": f.filing_date, "form": f.form, "items": f.items,
                         "accession": f.accession, "report": report, "source": SOURCE})
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["accepted_ts"] = pd.to_datetime(df["accepted_ts"], utc=True)
    for c in ("session", "last_close_before", "filing_date"):
        df[c] = pd.to_datetime(df[c]).dt.date
    return df.sort_values(["company", "accepted_ts"], ignore_index=True)


def tz_check(df) -> dict:
    """acceptanceDateTime should be UTC. EDGAR dates an 8-K accepted after 17:30 ET on the next business day and
    accepts filings only 06:00-22:00 ET: under the UTC reading both should hold for (nearly) every row."""
    import pandas as pd

    if not len(df):
        return {"rows": 0}
    et = pd.to_datetime(df["accepted_ts"], utc=True).dt.tz_convert(nyse.ET)
    mins = et.dt.hour * 60 + et.dt.minute
    early = mins < 17 * 60 + 30
    same = et.dt.date == pd.to_datetime(df["filing_date"]).dt.date
    in_hours = ((mins >= 6 * 60) & (mins < 22 * 60)).mean()
    agree = float((same[early]).mean()) if early.any() else None
    return {"rows": int(len(df)), "share_in_edgar_hours": round(float(in_hours), 4),
            "share_filing_date_matches_before_1730": None if agree is None else round(agree, 4),
            "utc_reading_ok": bool(in_hours >= 0.99 and (agree is None or agree >= 0.95))}
