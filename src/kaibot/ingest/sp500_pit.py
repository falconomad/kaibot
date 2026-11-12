"""Point-in-time S&P 500 membership, 2016 -> today (story S83, issue #123, audit E3).

Research data only: the live engine never imports this module.

Why: the research universe (config/universe_history.yaml) is today's 288 large caps applied back to 2016, which flatters
any rule that buys strength. This module answers "which companies were in the S&P 500 on day T", including the ones
that were later removed, acquired or went bust, and names the bar-store key (story S84, #124) of each of them.

Committed data (src/kaibot/ingest/sp500/, all small):
  changes.csv     the dated change log: the first row is the full list on the first session of 2016, every later row
                  the tickers added and removed with effect from that date (point-in-time tickers, as listed then)
  ticker_map.csv  which company a ticker denotes on a date (renames, reused tickers, two fixes of the source)
  supplement.csv  changes after the last date of the upstream file, from S&P Dow Jones Indices releases
  outcomes.csv    known ends of listed life (bank failures, takeovers) for the hole policy

Sources: the public GitHub file fja05680/sp500 "S&P 500 Historical Components & Changes (Updated).csv" (Andreas
Clenow's list 1996-2019, then updated from Wikipedia's change table), plus supplement.csv.
`scripts/fetch_sp500_history.py --update` rebuilds changes.csv from the upstream file; the method and its limits
are in docs/ops/pit_universe.md.

Names used here:
  ticker      a symbol as listed in the index on a date (FB in 2018, META in 2023)
  company     one listed company over time: today's ticker (META), or TICKER#YEAR when its ticker was later reused by
              another company (DOW#2017 is Dow Chemical; DOW is Dow Inc., spun off in 2019)
  key         the column / file name of the company in the daily bar store: today's research-universe ticker for the
              288 names, else SYMBOL@ASOF (the ticker requested as of a date inside its membership; #124's contract)
"""

from __future__ import annotations

import bisect
import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

PKG = Path(__file__).with_name("sp500")
CHANGES = PKG / "changes.csv"
TICKER_MAP = PKG / "ticker_map.csv"
SUPPLEMENT = PKG / "supplement.csv"
OUTCOMES = PKG / "outcomes.csv"
ROOT = Path(__file__).resolve().parents[3]
UNIVERSE = ROOT / "config" / "universe_history.yaml"

START = date(2016, 1, 1)  # Alpaca's history (and #124's store) begins in 2016
SOURCE_URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
              "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv")
SOURCE_NAME = "fja05680/sp500"
FILE_HEADER = ["symbol", "asof_date"]  # the --symbols-file contract with S84 (#124): do not change
# A second share class of a company already in the list: the index holds both, the research universe one (the more
# liquid class). Dropping them keeps one column, one position and one vote per company.
SECONDARY_CLASSES = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA", "UA": "UAA", "DISCK": "DISCA"}
ASOF_LAG_DAYS = 7  # the as-of date sits this far before the last membership day (inside the spell, before delisting)
DEFAULT_TERMINAL_RETURN = -0.30  # hole policy: a held company whose bars stop, without a known outcome


def _rows(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def _tickers(s: str) -> tuple[str, ...]:
    return tuple(t.strip().upper() for t in (s or "").split(",") if t.strip())


# ---------------------------------------------------------------- the change log


@dataclass(frozen=True)
class Change:
    """Index change in effect from `date` (before that day's open). The first change of a log is the base list."""

    date: date
    added: tuple[str, ...]
    removed: tuple[str, ...] = ()
    source: str = SOURCE_NAME


def parse_source(text: str) -> list[tuple[date, frozenset[str]]]:
    """The upstream CSV (`date,tickers`: the full list on each date it changed) -> sorted (date, tickers)."""
    out: dict[date, frozenset[str]] = {}
    for r in csv.DictReader(io.StringIO(text)):
        out[date.fromisoformat(r["date"].strip())] = frozenset(_tickers(r["tickers"]))
    if not out:
        raise ValueError("no rows in the source file")
    return sorted(out.items())


def change_log(snapshots: Sequence[tuple[date, frozenset[str]]], start: date = START) -> list[Change]:
    """Full lists by date -> base list on the first date >= start, then one Change per date the list changed."""
    i = next((k for k, (d, _) in enumerate(snapshots) if d >= start), None)
    if i is None:
        raise ValueError(f"the source has no list on or after {start}")
    d0, cur = snapshots[i]
    if i > 0 and snapshots[i - 1][1] != cur:  # the list in force on `start` is the one before the first change
        d0, cur = start, snapshots[i - 1][1]
        i -= 1
    log = [Change(d0, tuple(sorted(cur)))]
    for d, s in snapshots[i + 1:]:
        if s != cur:
            log.append(Change(d, tuple(sorted(s - cur)), tuple(sorted(cur - s))))
            cur = s
    return log


def read_supplement(path: Path = SUPPLEMENT) -> list[Change]:
    return [Change(date.fromisoformat(r["date"]), _tickers(r["added"]), _tickers(r["removed"]), r["source"].strip())
            for r in _rows(path)]


def merge_supplement(log: Sequence[Change], supplement: Iterable[Change]) -> tuple[list[Change], list[Change]]:
    """Append the supplement rows dated after the log's last change. Returns (merged log, rows skipped as covered).

    Each appended row is checked: what it removes must be in the list, what it adds must not be."""
    out = list(log)
    cur = members_after(out)
    skipped: list[Change] = []
    for ch in sorted(supplement, key=lambda c: c.date):
        if ch.date <= out[-1].date:
            skipped.append(ch)
            continue
        bad = [t for t in ch.removed if t not in cur] + [t for t in ch.added if t in cur]
        if bad:
            raise ValueError(f"supplement {ch.date}: {', '.join(bad)} do not fit the list in force before it")
        cur = (cur - set(ch.removed)) | set(ch.added)
        out.append(ch)
    return out, skipped


def members_after(log: Sequence[Change]) -> set[str]:
    cur: set[str] = set()
    for ch in log:
        cur = (cur - set(ch.removed)) | set(ch.added)
    return cur


def write_changes(log: Sequence[Change], path: Path = CHANGES) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", newline="") as fh:
        fh.write("# S&P 500 membership changes (story S83, #123). Row 1: the full list in force on that date. Later\n"
                 "# rows: tickers added / removed with effect from that date (before the open). Point-in-time\n"
                 "# tickers; see ticker_map.csv for renames. Rebuild: scripts/fetch_sp500_history.py --update\n")
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "added", "removed", "source"])
        for ch in log:
            w.writerow([ch.date.isoformat(), ",".join(ch.added), ",".join(ch.removed), ch.source])
    tmp.replace(p)


def read_changes(path: Path = CHANGES) -> list[Change]:
    log = [Change(date.fromisoformat(r["date"]), _tickers(r["added"]), _tickers(r["removed"]), r["source"].strip())
           for r in _rows(path)]
    if not log:
        raise ValueError(f"{path}: empty change log")
    if any(b.date <= a.date for a, b in zip(log, log[1:], strict=False)):
        raise ValueError(f"{path}: dates must increase")
    return log


# ---------------------------------------------------------------- tickers -> companies


@dataclass(frozen=True)
class Alias:
    ticker: str
    until: date
    company: str
    kind: str = "rename"
    note: str = ""


def read_ticker_map(path: Path = TICKER_MAP) -> dict[str, list[Alias]]:
    out: dict[str, list[Alias]] = {}
    for r in _rows(path):
        a = Alias(r["ticker"].strip().upper(), date.fromisoformat(r["until"]), r["company"].strip().upper(),
                  r.get("kind", "").strip(), r.get("note", "").strip())
        out.setdefault(a.ticker, []).append(a)
    for v in out.values():
        v.sort(key=lambda a: a.until)
    return out


def read_outcomes(path: Path = OUTCOMES) -> dict[str, tuple[float, str]]:
    return {r["company"].strip().upper(): (float(r["terminal_return"]), r["outcome"].strip()) for r in _rows(path)}


def read_universe(path: Path = UNIVERSE) -> list[str]:
    """The 288 stocks of config/universe_history.yaml (no ETFs, no SPY)."""
    import yaml

    cfg = yaml.safe_load(Path(path).read_text())
    return [str(s) for syms in cfg["sectors"].values() for s in syms]


@dataclass(frozen=True)
class Spell:
    """One continuous membership of a company: member on start <= d < end (end None: still a member)."""

    company: str
    start: date
    end: date | None
    tickers: tuple[str, ...]  # point-in-time tickers used during the spell, in order

    def covers(self, d: date) -> bool:
        return self.start <= d and (self.end is None or d < self.end)

    @property
    def last_day(self) -> date | None:
        return None if self.end is None else self.end - timedelta(days=1)


@dataclass
class Membership:
    """Who was in the S&P 500 on each date from the first change of the log on.

    >>> m = Membership.load()
    >>> "SIVB" in m.companies_on(date(2022, 6, 30)), "SIVB" in m.companies_on(date(2023, 6, 30))
    (True, False)
    """

    changes: list[Change]
    aliases: dict[str, list[Alias]] = field(default_factory=dict)
    universe: Sequence[str] = ()  # today's research universe (288): their keys are plain tickers
    today: date | None = None

    def __post_init__(self) -> None:
        self.today = self.today or date.today()
        self._dates: list[date] = []
        self._lists: list[frozenset[str]] = []
        cur: set[str] = set()
        for ch in self.changes:
            cur = (cur - set(ch.removed)) | set(ch.added)
            self._dates.append(ch.date)
            self._lists.append(frozenset(cur))
        self._universe = set(self.universe)
        self.spells: dict[str, list[Spell]] = self._build_spells()

    @classmethod
    def load(cls, changes: Path = CHANGES, ticker_map: Path = TICKER_MAP, universe: Path | None = UNIVERSE,
             today: date | None = None) -> Membership:
        uni = read_universe(universe) if universe is not None and Path(universe).is_file() else []
        return cls(read_changes(changes), read_ticker_map(ticker_map), uni, today)

    # ------------------------------------------------------------ point-in-time lists

    @property
    def first(self) -> date:
        return self._dates[0]

    def _at(self, d: date) -> frozenset[str]:
        if d < self._dates[0]:
            raise ValueError(f"{d} is before the first list of the change log ({self._dates[0]})")
        return self._lists[bisect.bisect_right(self._dates, d) - 1]

    def members_on(self, d: date) -> set[str]:
        """Tickers in the index on `d` as listed then (both share classes where there are two; the source's
        duplicate listings of ticker_map.csv `dup` rows dropped)."""
        d = _day(d)
        lst = self._at(d)
        return {t for t in lst if not self._dup(t, d, lst)}

    def _dup(self, t: str, d: date, lst: frozenset[str]) -> bool:
        if not any(a.kind == "dup" and d <= a.until for a in self.aliases.get(t, ())):
            return False
        c = self.company_of(t, d)
        return any(o != t and self.company_of(o, d) == c for o in lst)

    def company_of(self, ticker: str, d: date) -> str:
        for a in self.aliases.get(ticker, ()):
            if d <= a.until:
                return a.company if a.company == ticker else self.company_of(a.company, a.until + timedelta(1))
        return ticker

    def companies_on(self, d: date) -> set[str]:
        """Companies in the index on `d` (company ids; one per company: second share classes dropped)."""
        d = _day(d)
        return {self.company_of(t, d) for t in self.members_on(d) if t not in SECONDARY_CLASSES}

    def _build_spells(self) -> dict[str, list[Spell]]:
        open_: dict[str, tuple[date, list[str]]] = {}
        out: dict[str, list[Spell]] = {}
        for d, lst in zip(self._dates, self._lists, strict=True):
            now: dict[str, str] = {}
            for t in sorted(lst):
                if t not in SECONDARY_CLASSES and not self._dup(t, d, lst):
                    now.setdefault(self.company_of(t, d), t)
            for c in [c for c in open_ if c not in now]:
                s, ts = open_.pop(c)
                out.setdefault(c, []).append(Spell(c, s, d, tuple(ts)))
            for c, t in now.items():
                if c not in open_:
                    open_[c] = (d, [t])
                elif open_[c][1][-1] != t:
                    open_[c][1].append(t)
        for c, (s, ts) in open_.items():
            out.setdefault(c, []).append(Spell(c, s, None, tuple(ts)))
        return dict(sorted(out.items()))

    def current(self) -> set[str]:
        return {c for c, sp in self.spells.items() if sp[-1].end is None}

    # ------------------------------------------------------------ bar-store keys

    def request(self, company: str) -> tuple[str, date]:
        """(symbol, as-of date) to download a company's bars: its last ticker in the index, as of a date inside its
        last spell (ASOF_LAG_DAYS before the last membership day, so before any delisting)."""
        sp = self.spells[company][-1]
        last = sp.last_day or self.today
        asof = max(sp.start, START, last - timedelta(days=ASOF_LAG_DAYS))
        return sp.tickers[-1], min(asof, self.today)

    def universe_ticker(self, company: str) -> str | None:
        """The research-universe (288) ticker of a company, if it is one of them."""
        if "#" in company:
            return None
        if company in self._universe:
            return company
        seen = [t for sp in self.spells.get(company, ()) for t in sp.tickers if t in self._universe]
        return seen[-1] if seen else None

    def keys(self, company: str) -> list[str]:
        """Bar-store keys for a company, preferred first: the 288 ticker (if any), then SYMBOL@ASOF."""
        sym, asof = self.request(company)
        u = self.universe_ticker(company)
        return ([u] if u else []) + [f"{sym}@{asof.isoformat()}"]

    def needs_request(self, company: str) -> bool:
        """Every company except a current member downloaded under the same ticker with today's universe."""
        u = self.universe_ticker(company)
        return not (u is not None and u == company and self.spells[company][-1].end is None)

    def former_members(self) -> list[tuple[str, date]]:
        """Lines of data/universe/former_members.csv: every company in the index since 2016 that #124 does not
        already download as one of today's 288 (former members, current members outside the 288, and renamed or
        departed 288 names as a second chance), as (symbol, asof_date), sorted."""
        return sorted(self.request(c) for c in self.spells if self.needs_request(c))

    def key_map(self, columns: Iterable[str]) -> dict[str, int]:
        """company -> column index in `columns` (DailyBars.symbols), first preferred key that is present."""
        col = {s: j for j, s in enumerate(columns)}
        out: dict[str, int] = {}
        for c in self.spells:
            j = next((col[k] for k in self.keys(c) if k in col), None)
            if j is not None:
                out[c] = j
        return out

    def mask(self, dates: Sequence, columns: Sequence[str]) -> np.ndarray:
        """(sessions x columns) bool: True where the column's company was in the S&P 500 that session.

        Columns that are no member's key (ETFs, SPY, names never in the index) are all False. Dates before the first
        list of the log raise. This is the "members on T" half of the research universe; the liquidity filter on T
        stays with the harness."""
        import numpy as np

        days = [_day(d) for d in dates]
        out = np.zeros((len(days), len(columns)), dtype=bool)
        for c, j in self.key_map(columns).items():
            for sp in self.spells[c]:
                lo = bisect.bisect_left(days, sp.start)
                hi = len(days) if sp.end is None else bisect.bisect_left(days, sp.end)
                out[lo:hi, j] = True
        if days and days[0] < self.first:
            raise ValueError(f"{days[0]} is before the first list of the change log ({self.first})")
        return out

    # ------------------------------------------------------------ report numbers

    def yearly(self) -> list[dict]:
        """Per calendar year: companies at the start, at the end, distinct during, additions, removals."""
        years = range(self.first.year, self.today.year + 1)
        out = []
        for y in years:
            lo, hi = max(date(y, 1, 1), self.first), min(date(y, 12, 31), self.today)
            during = {c for c, sps in self.spells.items() for sp in sps
                      if sp.start <= hi and (sp.end is None or sp.end > lo)}
            adds = sum(1 for sps in self.spells.values() for sp in sps if lo < sp.start <= hi)
            rems = sum(1 for sps in self.spells.values() for sp in sps if sp.end is not None and lo < sp.end <= hi)
            out.append({"year": y, "start": len(self.companies_on(lo)), "end": len(self.companies_on(hi)),
                        "distinct": len(during), "added": adds, "removed": rems})
        return out


def terminal_return(company: str, outcomes: Mapping[str, tuple[float, str]] | None = None) -> float:
    """Hole policy: the return booked at the last stored bar of a held company whose bars stop while it is held."""
    outcomes = read_outcomes() if outcomes is None else outcomes
    return outcomes.get(company, (DEFAULT_TERMINAL_RETURN, ""))[0]


def write_symbols_file(rows: Iterable[tuple[str, date]], path: Path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    with p.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(FILE_HEADER)
        w.writerows([s, d.isoformat()] for s, d in rows)
    return len(rows)


def write_spells(m: Membership, path: Path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["company", "start", "end", "tickers", "keys"])
        for c, sps in m.spells.items():
            for sp in sps:
                w.writerow([c, sp.start.isoformat(), sp.end.isoformat() if sp.end else "", "|".join(sp.tickers),
                            "|".join(m.keys(c))])
                n += 1
    return n


# ---------------------------------------------------------------- cross-check against IVV's SEC filings

IVV_NAMES = PKG / "ivv_names.csv"
NPORT_NS = "{http://www.sec.gov/edgar/nport}"
_NAME_DROP = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "NV", "N", "V", "THE", "HOLDINGS",
              "HOLDING", "GROUP", "CLASS", "A", "B", "C", "CL", "SA", "LP", "LLC", "TRUST", "REIT", "INCORPORATED",
              "INTERNATIONAL", "INTL", "COS", "NEW", "DE", "AG", "SE", "LIMITED"}


def norm_name(s: str) -> str:
    import re

    s = re.sub(r"[^A-Z0-9 ]", " ", s.upper().replace("&", " AND ").replace("’", "'"))
    return " ".join(w for w in s.split() if w not in _NAME_DROP)


def read_nport(path: Path) -> tuple[date, list[str]]:
    """(report date, issuer names of the common-stock lines) of an N-PORT-P primary_doc.xml."""
    import xml.etree.ElementTree as ET

    root = ET.parse(path).getroot()
    d = date.fromisoformat(root.findtext(f".//{NPORT_NS}repPdDate").strip())
    names = [h.findtext(f"{NPORT_NS}name") or "" for h in root.iter(f"{NPORT_NS}invstOrSec")
             if h.findtext(f"{NPORT_NS}assetCat") == "EC"]
    return d, names


def crosscheck(m: Membership, d: date, names: Sequence[str], table: Path = IVV_NAMES) -> tuple[list[str], list[str]]:
    """Compare the list on `d` with a fund's common-stock lines (issuer names). Returns (tickers in the list with no
    holding, holdings with no ticker in the list); both empty means the same membership."""
    by_name: dict[str, set[str]] = {}
    for r in _rows(table):
        by_name.setdefault(norm_name(r["ivv_name"]), set()).add(r["ticker"])
    left = set(m.members_on(d))
    extra: list[str] = []
    for n in names:
        hit = sorted(by_name.get(norm_name(n), set()) & left)
        if hit:
            left.discard(hit[0])
        else:
            extra.append(n)
    return sorted(left), extra


# ---------------------------------------------------------------- the hole log (run on the Mac, after #124's fetch)


HOLE_COLUMNS = ["company", "key", "spell_start", "spell_end", "first_bar", "last_bar", "kind", "member_days_lost",
                "terminal_return", "note"]
HOLE_SLACK_DAYS = 10  # bars this close to a spell's start / end count as covering it (holidays, last trading day)


def _store_dates(store: Path, key: str) -> list[date] | None:
    p = Path(store) / "bars" / f"symbol={key}.parquet"
    if not p.is_file():
        return None
    import pyarrow.parquet as pq

    col = pq.read_table(p, columns=["date"]).column("date").to_pylist()
    return sorted(col) or None


def hole_log(m: Membership, store: Path, outcomes: Mapping[str, tuple[float, str]] | None = None) -> list[dict]:
    """One row per membership spell whose bars are missing or stop early in #124's store (data/daily).

    kind: no_bars (no key of the company has bars: it cannot be picked on any day of the spell), starts_late (bars
    begin well after the spell starts: not pickable until then), ends_early (bars stop well before the spell ends: a
    position held then is closed at the last bar at `terminal_return`). Spells before 2016 are clipped to 2016."""
    outcomes = read_outcomes() if outcomes is None else outcomes
    slack = timedelta(days=HOLE_SLACK_DAYS)
    rows: list[dict] = []
    for c, sps in m.spells.items():
        key, days = next(((k, ds) for k in m.keys(c) if (ds := _store_dates(store, k))), (m.keys(c)[-1], None))
        tr, why = outcomes.get(c, (DEFAULT_TERMINAL_RETURN, "no known outcome: default"))
        for sp in sps:
            s, e = max(sp.start, START), sp.last_day or m.today
            base = {"company": c, "key": key, "spell_start": s.isoformat(), "spell_end": e.isoformat(),
                    "first_bar": "", "last_bar": "", "terminal_return": "", "note": ""}
            if days is None or days[-1] < s or days[0] > e:
                rows.append({**base, "kind": "no_bars", "member_days_lost": (e - s).days + 1,
                             "first_bar": days[0].isoformat() if days else "",
                             "last_bar": days[-1].isoformat() if days else "",
                             "note": "not pickable during the spell"})
                continue
            first, last = days[bisect.bisect_left(days, s)], days[bisect.bisect_right(days, e) - 1]
            base.update(first_bar=first.isoformat(), last_bar=last.isoformat())
            if first > s + slack:
                rows.append({**base, "kind": "starts_late", "member_days_lost": (first - s).days,
                             "note": "not pickable before the first bar"})
            if last < e - slack:
                rows.append({**base, "kind": "ends_early", "member_days_lost": (e - last).days,
                             "terminal_return": tr, "note": why})
    return rows


def write_hole_log(rows: Sequence[dict], path: Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, HOLE_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _day(d) -> date:
    if isinstance(d, date):
        return d.date() if hasattr(d, "hour") else d
    if type(d).__name__ == "datetime64":  # numpy, without importing it here
        return date.fromisoformat(str(d.astype("datetime64[D]")))
    return date.fromisoformat(str(d)[:10])
