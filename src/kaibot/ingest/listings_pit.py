"""Point-in-time reader of the new-listing watch (story S102, #376). Shadow data: the engine never reads it.

`Listings` folds the observations of `kaibot.ingest.alt.listings` (data/alt/listings) into one row per listing
candidate, using only what was available at a decision time:

- `table(as_of)`: symbol, company, CIK, what kind of event it is, whether it counts as a new listing, its status
  (registered, priced, expected, pre_listing, listed) and its first trading session with the source of that date;
- `listing_age_sessions(symbol, as_of)`: how many regular sessions the listing has traded, the first one counted
  as 1. A feature for research: a later rule can exclude young listings by rule;
- `first_eligible(first_session)`: for each existing rule, the first date it could consider the stock, from the
  rule's own warm-up (design for M8, section 3.2). The numbers are copied here by hand, because the data package
  may not import the rules; tests/unit/test_listings.py compares them with the rules' own code.

Only an initial public offering or a direct listing counts as a new listing. A fund, a unit, a warrant, a right,
a preferred share, a blank-check company, a spin-off and a ticker change are recognised and never reported as
one; a new symbol nothing is known about is `unknown`, and is not reported either.

The history for research (`ipo_history`, built by scripts/fetch_listings.py into data/listings/ipos.parquet) comes
from EDGAR's quarterly indexes: a 424B4 with an exchange registration (8-A12B) around it, joined to the first
daily bar of the store when the store has the symbol.

Two readings of "known at `as_of`": by default a row counts from `available_ts`, the time it was public (research);
with `live=True` it counts from the later of that and `ingested_ts`, the time this machine stored it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa

from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.alt import listings as ls
from kaibot.ingest.alt import store
from kaibot.ingest.sp500_pit import norm_name

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
HISTORY = Path("listings") / "ipos.parquet"
NEW_LISTING_KINDS = ("ipo", "direct_listing")
TABLE_COLUMNS = ["symbol", "company", "cik", "exchange", "kind", "is_new_listing", "status", "first_session",
                 "first_session_source", "offer_price", "shares_offered", "pricing_date", "known_since", "evidence"]
SP500_MIN_MONTHS = 12  # S&P asks for 12 months of trading before a company can join (design, section 3.3)

# What a stock needs before each rule can consider it (design for M8, section 3.2). `sessions`: the count of
# sessions that completes the rule's own history, the first trading session counted as 1; 0 = no count of its own.
RULES: list[dict] = [
    {"rule": "orb_stocks_in_play", "state": "shadow", "sessions": 15, "needs_sp500": False,
     "needs": "in the live list; average volume >= 1,000,000 shares; a 14-session range above $0.50"},
    {"rule": "news_continuation", "state": "shadow", "sessions": 21, "needs_sp500": False,
     "needs": "in the live list; 21 sessions of minute bars"},
    {"rule": "owner_tickers", "state": "the owner's own ticker list", "sessions": 60, "needs_sp500": False,
     "needs": "dollar volume >= $50M and price >= $5 (engine/tickercheck.py)"},
    {"rule": "trend_momentum", "state": "on paper", "sessions": 100, "needs_sp500": True,
     "needs": "S&P 500 membership on the day; 100 closes; no one-day move above 15% in the last 90 returns; "
              "close >= $5; 20-day average dollar volume >= $50M"},
    {"rule": "mom_12_1_band", "state": "off", "sessions": 273, "needs_sp500": True,
     "needs": "S&P 500 membership; 273 sessions (twelve months plus one)"},
    {"rule": "post_earnings_week", "state": "pre-registered", "sessions": 0, "needs_sp500": True,
     "needs": "S&P 500 membership; then the first qualifying event"},
    {"rule": "news_gap_week", "state": "proposed", "sessions": 0, "needs_sp500": True,
     "needs": "S&P 500 membership; then the first qualifying event"},
]

_FUND = re.compile(r"\b(ETFS?|ETNS?|FUND|INDEX|PORTFOLIO)\b", re.I)
_DERIVED = re.compile(r"\b(UNITS?|WARRANTS?|RIGHTS?|PREFERRED|NOTES?|DEBENTURES?)\b|\d%", re.I)
_SUFFIX = re.compile(r"\.(U|UN|WS|W|R|RT|PR[A-Z]?)$")
_SPAC = re.compile(r"\bACQUISITION\b|\bBLANK CHECK\b|\bSPAC\b", re.I)
_DESCRIPTION = frozenset({"COMMON", "STOCK", "ORDINARY", "SHARES", "SHARE", "AMERICAN", "DEPOSITARY", "ADS", "ADR",
                          "EACH", "REPRESENTING", "ONE", "PAR", "VALUE", "VOTING", "NON", "SUBORDINATE", "CAPITAL",
                          "OF", "BENEFICIAL", "INTEREST", "PER", "NO"})


def _ts(as_of) -> pd.Timestamp:
    t = pd.Timestamp(as_of)
    if t.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (a decision time)")
    return t.tz_convert(UTC)


def _links(edgar_name: str, other: str) -> bool:
    """The other source's security name is EDGAR's company name plus words that only describe the security
    ("Spinco Inc." and "Spinco Inc. Common Stock"; not "Apple Inc." and "Apple Hospitality REIT")."""
    a, b = norm_name(edgar_name).split(), norm_name(other).split()
    return (len("".join(a)) >= 4 and b[:len(a)] == a
            and all(t in _DESCRIPTION or t.isdigit() for t in b[len(a):]))


def _session_on_or_after(d: date) -> date:
    while not nyse.is_session(d):
        d += timedelta(days=1)
    return d


def nth_session(first_session: date, n: int) -> date:
    """The n-th regular session of a listing, its first session counted as 1."""
    return nyse.sessions(first_session, first_session + timedelta(days=2 * n + 30))[n - 1]


def first_eligible(first_session: date) -> list[dict]:
    """For each rule of `RULES`: `history_complete`, the session that completes the rule's own history (None when
    it has no count of its own); `index_earliest`, the first session on or after 12 months of trading, the
    earliest S&P's rules allow the company into the index (None for a rule that does not need the index); and
    `first_eligible`, the later of the two. It is an earliest date, not a forecast: S&P decides membership (it
    also asks for a public float of at least 10%), and the other conditions under `needs` must hold too."""
    try:
        year_later = first_session.replace(year=first_session.year + SP500_MIN_MONTHS // 12)
    except ValueError:  # 29 February
        year_later = first_session.replace(year=first_session.year + 1, day=28)
    index_earliest = _session_on_or_after(year_later)
    out = []
    for r in RULES:
        done = nth_session(first_session, r["sessions"]) if r["sessions"] else None
        idx = index_earliest if r["needs_sp500"] else None
        out.append({"rule": r["rule"], "state": r["state"], "sessions": r["sessions"], "history_complete": done,
                    "index_earliest": idx, "first_eligible": max(d for d in (done, idx) if d is not None),
                    "needs": r["needs"]})
    return out


def _pick(rows: list[dict], column: str):
    return next((r[column] for r in reversed(rows) if r[column] is not None), None)


def _clean(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return df.astype(object).where(df.notna(), None).to_dict("records")


class Listings:
    def __init__(self, events: pd.DataFrame, history: pd.DataFrame | None = None):
        e = events.copy()
        for c in ("available_ts", "ingested_ts", "published_ts", "accepted_ts"):
            if c in e:
                e[c] = pd.to_datetime(e[c], utc=True)
        self.events = e[e["source"] != "collector"] if len(e) else e
        self.history = history
        self._last: tuple | None = None  # the last fold: table, listing and evidence often ask at one time

    @classmethod
    def load(cls, data_root: Path = DATA) -> Listings:
        events = store.read_source(Path(data_root), ls.SOURCE)
        if events.empty:
            events = pd.DataFrame(columns=ls.SCHEMA.names)
        hist = Path(data_root) / HISTORY
        return cls(events, pd.read_parquet(hist) if hist.is_file() else None)

    # ------------------------------------------------------------ what was known

    def versions(self, as_of, live: bool = False) -> pd.DataFrame:
        """Every stored row that was known at `as_of`, oldest first."""
        t, e = _ts(as_of), self.events
        if not len(e):
            return e
        when = e[["available_ts", "ingested_ts"]].max(axis=1) if live else e["available_ts"]
        return e[when <= t].sort_values(["available_ts", "ingested_ts", "key"], kind="stable")

    def _fold(self, as_of, live: bool) -> list[dict]:
        t = _ts(as_of)
        if self._last is None or self._last[0] != (t, live):
            self._last = ((t, live), self._fold_now(t, live))
        return self._last[1]

    def _fold_now(self, t: pd.Timestamp, live: bool) -> list[dict]:
        v = self.versions(t, live)
        if not len(v):
            return []
        rows = _clean(ls.latest(v))
        by_source: dict[str, list[dict]] = {}
        for r in rows:
            by_source.setdefault(r["source"], []).append(r)
        groups: dict[str, list[dict]] = {}
        for r in by_source.get("edgar_index", []):
            groups.setdefault(r["cik"], []).append(r)
        cikmap = {r["cik"]: r for r in by_source.get("sec_submissions", [])}
        symbol_cik = {r["symbol"]: c for c, r in cikmap.items() if r["symbol"]}
        by_first_word: dict[str, list[str]] = {}  # EDGAR company names, to find a security's company by its name
        for cik, g in groups.items():
            words = norm_name(g[0]["company"]).split()
            if words:
                by_first_word.setdefault(words[0], []).append(cik)

        # the Alpaca side: every version of every asset, to see earlier symbols and the `ipo` attribute
        seeds: dict[str, dict] = {}

        def seed(symbol: str) -> dict:
            return seeds.setdefault(symbol, {"names": [], "alpaca": [], "alpaca_new": None, "renamed": False,
                                             "ipo_attr": False, "ipo_now": False})

        history: dict[str, list[dict]] = {}
        for r in _clean(v[v["source"] == "alpaca_assets"]):
            history.setdefault(r["key"], []).append(r)
        names_before: dict[str, set[str]] = {}  # security name -> the symbols it was first seen under
        for vers in history.values():
            names_before.setdefault(vers[0]["company"].upper(), set()).add(vers[0]["symbol"])
        for key, vers in history.items():
            first, last = vers[0], vers[-1]
            renamed = bool(first["baseline"]) and len({x["symbol"] for x in vers}) > 1
            if (first["baseline"] and not renamed) or last["exchange"] in ls.UNLISTED:
                continue  # there before the first night, or not on an exchange
            s = seed(last["symbol"])
            s["alpaca"].append(key)
            s["names"].append(last["company"])
            s["renamed"] |= renamed
            s["ipo_attr"] |= any("ipo" in (x["attributes"] or "").split(",") for x in vers)
            s["ipo_now"] = "ipo" in (last["attributes"] or "").split(",")
            trading = [x for x in vers if x["kind"] != "gone" and x["tradable"]
                       and "ipo" not in (x["attributes"] or "").split(",")]
            new_at = next(x["available_ts"] for x in vers if x["symbol"] == last["symbol"])
            s["alpaca_new"] = new_at if s["alpaca_new"] is None else min(s["alpaca_new"], new_at)
            s["seen_trading"] = trading[0]["available_ts"] if trading else None
            s["same_name_elsewhere"] = bool(names_before.get(last["company"].upper(), set()) - {last["symbol"]})
        for r in by_source.get("nasdaq_notice", []):
            if r["symbol"]:
                seed(r["symbol"])["names"].append(r["company"])

        def seeded(cik: str) -> bool:
            """An exchange registration with a final prospectus around it, or after a registration statement."""
            g = groups[cik]
            reg = [r["filed"] for r in g if r["form"] == ls.REGISTRATION]
            priced = [r["filed"] for r in g if r["form"] == ls.PROSPECTUS]
            plan = [r["filed"] for r in g if r["form"] in ls.PLAN_FORMS]
            return any(any(abs((d - p).days) <= 60 for p in priced) or any(0 <= (d - p).days <= 180 for p in plan)
                       for d in reg)

        taken: set[str] = set()
        out = []
        candidates: list[tuple[str, str | None]] = []
        for symbol in sorted(seeds):
            cik = symbol_cik.get(symbol)
            if cik is None:  # no ticker on file yet: the company whose EDGAR name the security's name carries
                firsts = {(norm_name(n).split() or [""])[0] for n in seeds[symbol]["names"]}
                near = {c for w in firsts for c in by_first_word.get(w, [])}
                hits = [c for c in sorted(near)
                        if any(n and _links(groups[c][0]["company"], n) for n in seeds[symbol]["names"])]
                cik = hits[0] if len(hits) == 1 else None
            if cik is not None:
                taken.add(cik)
            candidates.append((symbol, cik))
        for cik in sorted(groups):
            if cik not in taken and seeded(cik):
                symbol = (cikmap.get(cik) or {}).get("symbol") or ""
                if symbol and symbol in seeds:
                    continue
                candidates.append((symbol, cik))

        open_alpaca: dict[str, list[dict]] = {}
        for r in by_source.get("alpaca_assets", []):
            open_alpaca.setdefault(r["symbol"], []).append(r)
        for symbol, cik in candidates:
            s = seeds.get(symbol) or {"names": [], "alpaca": [], "alpaca_new": None, "renamed": False,
                                      "ipo_attr": False, "ipo_now": False}
            g = groups.get(cik, []) if cik else []
            ev = list(g)
            if cik in cikmap:
                ev.append(cikmap[cik])
            notices, bars, symdir = [], [], []
            if symbol:
                ev += open_alpaca.get(symbol, [])
                notices = [r for r in by_source.get("nasdaq_notice", []) if r["symbol"] == symbol]
                bars = [r for r in by_source.get("alpaca_bars", []) if r["symbol"] == symbol]
                symdir = [r for r in by_source.get("nasdaq_symdir", []) if r["symbol"] == symbol]
                ev += notices + bars + symdir
            forms = {r["form"] for r in g}
            names = [n for n in [*s["names"], *(r["company"] for r in symdir), *(r["company"] for r in g)] if n]
            hints = {r["attributes"] for r in notices}
            if any(r["test"] for r in symdir):
                kind = "test"
            elif any(r["etf"] for r in symdir) or any(_FUND.search(n) for n in names):
                kind = "fund"
            elif _SUFFIX.search(symbol) or any(_DERIVED.search(n) for n in names):
                kind = "unit_warrant_right_preferred"
            elif (s["renamed"] or s.get("same_name_elsewhere") or any(r["kind"] == "traded_before" for r in bars)):
                kind = "ticker_change"
            elif forms & ls.SPIN_FORMS and ls.PROSPECTUS not in forms:
                kind = "spin_off"
            elif any(_SPAC.search(n) for n in names):
                kind = "spac"
            elif "direct_listing" in hints:
                kind = "direct_listing"
            elif ("ipo" in hints or s["ipo_attr"]
                  or (ls.REGISTRATION in forms and forms & (ls.PLAN_FORMS | {ls.PROSPECTUS}))):
                kind = "ipo"
            else:
                kind = "unknown"
            first, source = None, ""
            bar = next((r for r in bars if r["kind"] == "first_bar"), None)
            dated = sorted((r for r in notices if r["first_trade_date"]), key=lambda r: r["available_ts"])
            if bar is not None:
                first, source = bar["first_trade_date"], "daily_bar"
            elif dated:
                first, source = dated[-1]["first_trade_date"], "nasdaq_notice"
            elif s.get("seen_trading") is not None and kind in (*NEW_LISTING_KINDS, "spin_off", "spac", "unknown"):
                first = _session_on_or_after(s["seen_trading"].tz_convert(nyse.ET).date())
                source = "alpaca_first_seen"
            if first is not None:
                status = "listed" if pd.Timestamp(nyse.open_utc(_session_on_or_after(first))) <= t else "expected"
            elif s["ipo_now"]:
                status = "pre_listing"
            elif ls.PROSPECTUS in forms:
                status = "priced"
            elif ls.REGISTRATION in forms:
                status = "registered"
            else:
                status = "unknown"
            priced = [r for r in g if r["form"] == ls.PROSPECTUS]
            company = next((n for n in [*(r["company"] for r in g), *(r["company"] for r in notices),
                                        *s["names"], *(r["company"] for r in symdir)] if n), "")
            exchange = next((x for x in [(cikmap.get(cik) or {}).get("exchange"),
                                         *(r["exchange"] for r in open_alpaca.get(symbol, []) if symbol)] if x), "")
            out.append({"symbol": symbol, "company": company, "cik": cik or "", "exchange": exchange, "kind": kind,
                        "is_new_listing": kind in NEW_LISTING_KINDS, "status": status, "first_session": first,
                        "first_session_source": source, "offer_price": _pick(priced, "offer_price"),
                        "shares_offered": _pick(priced, "shares_offered"),
                        "pricing_date": _pick(priced, "pricing_date"),
                        "known_since": min(r["available_ts"] for r in ev), "evidence": len(ev),
                        "_keys": sorted({r["key"] for r in ev}), "_alpaca_new": s["alpaca_new"]})
        return sorted(out, key=lambda r: (r["known_since"], r["symbol"], r["cik"]))

    # ------------------------------------------------------------ the answers

    def table(self, as_of, live: bool = False) -> pd.DataFrame:
        """One row per listing candidate known at `as_of` (TABLE_COLUMNS), oldest knowledge first."""
        return pd.DataFrame([{c: r[c] for c in TABLE_COLUMNS} for r in self._fold(as_of, live)],
                            columns=TABLE_COLUMNS)

    def listing(self, symbol: str, as_of, live: bool = False) -> dict | None:
        hit = next((r for r in self._fold(as_of, live) if symbol and r["symbol"] == symbol), None)
        return {c: hit[c] for c in TABLE_COLUMNS} if hit else None

    def evidence(self, symbol: str, as_of, live: bool = False) -> pd.DataFrame:
        """The stored observations behind a candidate, each with its own `available_ts`, oldest first."""
        hit = next((r for r in self._fold(as_of, live) if symbol and r["symbol"] == symbol), None)
        v = ls.latest(self.versions(as_of, live))
        keys = hit["_keys"] if hit else []
        return v[v["key"].isin(keys)].sort_values(["available_ts", "key"], kind="stable").reset_index(drop=True)

    def listing_age_sessions(self, symbol: str, as_of) -> int | None:
        """Regular sessions the listing has traded by `as_of`, its first session counted as 1 from that session's
        open. 0: a new listing is known and has not traded yet. None: nothing known at `as_of`, or the symbol is
        not a new listing (a ticker change keeps its age; the bar store has its history)."""
        t = _ts(as_of)
        first = None
        hit = next((r for r in self._fold(as_of, False) if r["symbol"] == symbol), None) if symbol else None
        if hit is not None and hit["is_new_listing"]:
            if hit["first_session"] is None:
                return 0
            first = hit["first_session"]
        elif self.history is not None and len(self.history):
            h = self.history
            h = h[(h["symbol"] == symbol) & (pd.to_datetime(h["available_ts"], utc=True) <= t)
                  & h["first_session"].notna() & h["kind"].isin(NEW_LISTING_KINDS)]
            if len(h):
                first = pd.Timestamp(h.sort_values("available_ts")["first_session"].iloc[-1]).date()
        if first is None:
            return None
        days = nyse.sessions(first, t.tz_convert(nyse.ET).date())
        return sum(1 for d in days if pd.Timestamp(nyse.open_utc(d)) <= t)


def listing_age_sessions(symbol: str, as_of, data_root: Path = DATA) -> int | None:
    """`Listings.load(data_root).listing_age_sessions(symbol, as_of)`: the point-in-time feature of S102."""
    return Listings.load(data_root).listing_age_sessions(symbol, as_of)


def report(listings: Listings, as_of, nights: int = 30) -> dict:
    """The false-alarm check of the issue: of the symbols that were new on Alpaca's list in the last `nights`
    nights, how many of each kind, and which were reported as a new listing."""
    t = _ts(as_of)
    since = t - pd.Timedelta(days=nights)
    new = [r for r in listings._fold(as_of, False) if r["_alpaca_new"] is not None and r["_alpaca_new"] > since]
    by_kind: dict[str, int] = {}
    for r in new:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    alp = listings.events[(listings.events["source"] == "alpaca_assets") & (listings.events["ingested_ts"] <= t)
                          & (listings.events["ingested_ts"] > since)] if len(listings.events) else listings.events
    collected = alp["ingested_ts"].dt.tz_convert(nyse.ET).dt.date.nunique() if len(alp) else 0
    return {"as_of": t.isoformat(), "nights_asked": nights, "nights_collected": int(collected),
            "alpaca_new_symbols": len(new), "by_kind": dict(sorted(by_kind.items())),
            "reported_as_new_listing": sorted(r["symbol"] for r in new if r["is_new_listing"])}


# ---------------------------------------------------------------- history from EDGAR's quarterly indexes

HISTORY_SCHEMA = pa.schema([
    ("company", pa.string()), ("cik", pa.string()), ("symbol", pa.string()), ("kind", pa.string()),
    ("accession", pa.string()), ("first_filed", pa.date32()), ("registration_filed", pa.date32()),
    ("prospectus_filed", pa.date32()), ("pricing_date", pa.date32()), ("offer_price", pa.float64()),
    ("shares_offered", pa.float64()), ("first_session", pa.date32()), ("first_session_source", pa.string()),
    ("available_ts", pa.timestamp("us", tz="UTC"))])
HISTORY_COLUMNS = HISTORY_SCHEMA.names


def cik_tickers(company_tickers: object) -> dict[str, str]:
    """company_tickers.json -> {10-digit CIK: today's ticker}; of several (share classes, units) the shortest."""
    out: dict[str, str] = {}
    vals = company_tickers.values() if isinstance(company_tickers, dict) else []
    for v in vals:
        try:
            cik, t = str(int(v["cik_str"])).zfill(10), str(v["ticker"]).upper()
        except (KeyError, TypeError, ValueError):
            continue
        if cik not in out or (len(t), t) < (len(out[cik]), out[cik]):
            out[cik] = t
    return out


def ipo_history(rows: Iterable[Mapping], tickers: Mapping[str, str],
                first_bar: Callable[[str, date], date | None] | None = None,
                covers: Mapping[str, Mapping] | None = None) -> pd.DataFrame:
    """One row per first sale of shares on an exchange since the start of the index rows: the first 424B4 of a
    company that has an exchange registration (8-A12B) from 60 days before to 10 days after it. A 424B4 without
    one is a later sale by a company that is already listed, and is left out.

    `symbol` is today's ticker of the CIK (empty when the company no longer trades). `first_session` is the first
    daily bar of the store when `first_bar(symbol, prospectus_filed)` has one near the prospectus; otherwise it
    stays empty. Offer price, shares and pricing date are filled only from `covers` ({accession: what
    `parse_prospectus_cover` read}); otherwise empty. Nothing is guessed. `kind` is `spac` for a blank-check
    company (by its name) and `ipo` otherwise: a direct listing cannot be told apart from the index alone."""
    by_cik: dict[str, list[Mapping]] = {}
    for r in rows:
        by_cik.setdefault(r["cik"], []).append(r)
    out = []
    for cik, g in by_cik.items():
        reg = sorted(r["filed"] for r in g if r["form"] == ls.REGISTRATION)
        for p in sorted((r for r in g if r["form"] == ls.PROSPECTUS), key=lambda r: (r["filed"], r["accession"])):
            near = [d for d in reg if -10 <= (p["filed"] - d).days <= 60]
            if not near:
                continue
            plan = [r["filed"] for r in g if r["form"] in ls.PLAN_FORMS and 0 <= (p["filed"] - r["filed"]).days <= 400]
            symbol = tickers.get(cik, "")
            first = first_bar(symbol, p["filed"]) if symbol and first_bar is not None else None
            cover = (covers or {}).get(p["accession"]) or {}
            avail = (nyse.close_utc(first) + ls.BAR_DELAY if first is not None and nyse.is_session(first)
                     else datetime.combine(p["filed"] + timedelta(days=1), ls.INDEX_DONE_ET, nyse.ET).astimezone(UTC))
            out.append({"company": p["company"], "cik": cik, "symbol": symbol,
                        "kind": "spac" if _SPAC.search(p["company"]) else "ipo", "accession": p["accession"],
                        "first_filed": min(plan) if plan else None, "registration_filed": max(near),
                        "prospectus_filed": p["filed"], "pricing_date": cover.get("prospectus_date"),
                        "offer_price": cover.get("offer_price"), "shares_offered": cover.get("shares_offered"),
                        "first_session": first, "first_session_source": "daily_bar" if first else "",
                        "available_ts": avail})
            break  # the first one only: later 424B4 filings of the same company are follow-on sales
    out.sort(key=lambda r: (r["prospectus_filed"], r["company"]))
    df = pd.DataFrame(out, columns=HISTORY_COLUMNS).astype({"offer_price": "float64", "shares_offered": "float64"})
    df["available_ts"] = pd.to_datetime(df["available_ts"], utc=True)
    return df


def write_history(df: pd.DataFrame, path: Path) -> None:
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(_clean(df), schema=HISTORY_SCHEMA)
    tmp = Path(path).with_suffix(".tmp")
    pq.write_table(table, tmp)
    tmp.replace(path)


def store_first_bar(daily_root: Path, slack_days: int = 10) -> Callable[[str, date], date | None]:
    """`first_bar` for `ipo_history` from the daily bar store (data/daily): the first stored bar of the symbol
    (or of a SYMBOL@ASOF key of it), if it lies within `slack_days` of the prospectus. A symbol whose bars begin
    long before is another, older listing under the same ticker; one the store does not hold gives None."""
    from kaibot.ingest.daily_bars import store_keys
    from kaibot.ingest.sp500_pit import _store_dates

    keys = store_keys(daily_root) if (Path(daily_root) / "bars").is_dir() else []

    def first_bar(symbol: str, filed: date) -> date | None:
        for key in keys:
            if key == symbol or key.startswith(f"{symbol}@"):
                dates = _store_dates(Path(daily_root), key)
                if dates and abs((dates[0] - filed).days) <= slack_days:
                    return dates[0]
        return None

    return first_bar

