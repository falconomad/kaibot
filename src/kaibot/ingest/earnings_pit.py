"""Point-in-time reader of earnings announcements (story S85, #125). Research data only; the live engine does
not read it.

`Announcements.next_known_announcement(symbol, as_of)` answers "what can a decision at `as_of` know about the
company's next earnings release?" from three kinds of evidence, best first, and never from a filing accepted
after `as_of` or a calendar snapshot taken after `as_of`:

1. `filed`: an 8-K Item 2.02 accepted at or before `as_of` whose first tradable session has not opened yet
   (a release after the close, read in the evening). Exact.
2. `scheduled`: the date in the latest forward-calendar snapshot taken at or before `as_of`
   (`ingest.earnings_calendar`, archived daily from Nov 2026 on). Exact to the day; the hour is bmo/amc/dmh/blank.
3. `estimate`: from the company's earlier reports only: one year (364 days) after the same quarter's report the
   year before, or 91 days after the last report; rolled forward a quarter while it is more than `POST_DAYS` in the
   past. The same rule as `replay/earnings.py`, but anchored on exact SEC times instead of headlines.

`last_known_announcement` gives the latest filing accepted at or before `as_of`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.earnings_sec import REPORT_GAP_DAYS

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data" / "earnings"
PRE_DAYS, POST_DAYS = 10, 10  # same window as replay/earnings.py


@dataclass(frozen=True)
class Known:
    symbol: str
    company: str
    kind: str  # filed | scheduled | estimate
    session: date  # first session that can trade on it (estimate: the expected report day)
    last_close_before: date  # be flat after this session's close to be out of the market for it
    known_since: datetime  # UTC: when the evidence became available (always <= as_of)
    accepted_ts: datetime | None = None
    hour: str = ""
    window: tuple[date, date] | None = None  # estimate only: (expected - PRE_DAYS, expected + POST_DAYS)


def _ts(as_of) -> pd.Timestamp:
    t = pd.Timestamp(as_of)
    if t.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (a decision time)")
    return t.tz_convert(UTC)


def _prev_session(d: date) -> date:
    d -= timedelta(days=1)
    while not nyse.is_session(d):
        d -= timedelta(days=1)
    return d


def _session_on_or_after(d: date) -> date:
    while not nyse.is_session(d):
        d += timedelta(days=1)
    return d


def expected_report(reports: Sequence[date], day: date) -> date | None:
    """Expected next report date from earlier report dates (ascending, all known on `day`)."""
    if not reports:
        return None
    last = reports[-1]
    year = timedelta(days=364)
    yr = [e + year for e in reports if last + timedelta(45) < e + year <= last + timedelta(135)]
    x = min(yr) if yr else last + timedelta(days=91)
    while x + timedelta(days=POST_DAYS) < day:
        x += timedelta(days=91)
    return x


class Announcements:
    def __init__(self, table: pd.DataFrame, calendar: pd.DataFrame | None = None, membership=None):
        t = table.copy()
        t["accepted_ts"] = pd.to_datetime(t["accepted_ts"], utc=True)
        self.table = t.sort_values("accepted_ts", ignore_index=True)
        cal = calendar if calendar is not None else pd.DataFrame(columns=["snapshot_ts", "symbol", "date", "hour"])
        cal = cal.copy()
        cal["snapshot_ts"] = pd.to_datetime(cal["snapshot_ts"], utc=True)
        self.calendar = cal.sort_values("snapshot_ts", ignore_index=True)
        self.membership = membership
        self._by_company = {c: g for c, g in self.table.groupby("company", sort=False)}

    @classmethod
    def load(cls, data_dir: Path = DATA, with_membership: bool = True) -> Announcements:
        from kaibot.ingest.earnings_calendar import read_snapshots

        m = None
        if with_membership:
            from kaibot.ingest.sp500_pit import Membership

            m = Membership.load()
        return cls(pd.read_parquet(Path(data_dir) / "announcements.parquet"),
                   read_snapshots(Path(data_dir) / "calendar"), m)

    # ------------------------------------------------------------ which company a symbol means

    def _company_id(self, symbol: str, as_of) -> str | None:
        """The company a symbol means at `as_of`, decided without looking at any filing accepted after `as_of`:
        a company id, a bar-store key (SYMBOL@ASOF) or a ticker as it was on that day."""
        t = _ts(as_of)
        if "#" in symbol:
            return symbol
        ticker, _, asof = symbol.partition("@")
        d = date.fromisoformat(asof) if asof else t.tz_convert(nyse.ET).date()
        if self.membership is not None:
            return self.membership.company_of(ticker, d)
        if symbol in self._by_company:
            return symbol
        k = self.table[(self.table["accepted_ts"] <= t) & (self.table["symbol"] == ticker)]
        hits = k["company"].unique()
        return str(hits[0]) if len(hits) == 1 else None

    def company(self, symbol: str, as_of) -> str | None:
        c = self._company_id(symbol, as_of)
        return c if c in self._by_company else None

    def known(self, symbol: str, as_of) -> pd.DataFrame:
        """The company's filings accepted at or before `as_of` (point-in-time)."""
        t = _ts(as_of)
        c = self._company_id(symbol, as_of)
        if c not in self._by_company:
            return self.table.iloc[0:0]
        g = self._by_company[c]
        return g[g["accepted_ts"] <= t]

    def last_known_announcement(self, symbol: str, as_of) -> Known | None:
        k = self.known(symbol, as_of)
        if not len(k):
            return None
        r = k.iloc[-1]
        return Known(str(r["symbol"]), str(r["company"]), "filed", r["session"], r["last_close_before"],
                     r["accepted_ts"].to_pydatetime(), r["accepted_ts"].to_pydatetime())

    def next_known_announcement(self, symbol: str, as_of) -> Known | None:
        t = _ts(as_of)
        day = t.tz_convert(nyse.ET).date()
        k = self.known(symbol, as_of)
        c = self._company_id(symbol, as_of)
        if len(k):
            r = k.iloc[-1]
            if pd.Timestamp(nyse.open_utc(r["session"])) > t:
                return Known(str(r["symbol"]), str(r["company"]), "filed", r["session"], r["last_close_before"],
                             r["accepted_ts"].to_pydatetime(), r["accepted_ts"].to_pydatetime())
        last_report = k["accepted_ts"].iloc[-1].tz_convert(nyse.ET).date() if len(k) else None
        sym = str(k["symbol"].iloc[-1]) if len(k) else symbol.partition("@")[0]
        sched = self._scheduled(c, symbol, sym, t, day, last_report)
        if sched is not None:
            return sched
        if c is None or not len(k):
            return None
        reps = [ts.tz_convert(nyse.ET).date() for ts in k.loc[k["report"].astype(bool), "accepted_ts"]]
        x = expected_report(reps, day)
        if x is None:
            return None
        s = _session_on_or_after(x)
        src = k.loc[k["report"].astype(bool)].iloc[-1]
        return Known(sym, c, "estimate", s, _prev_session(s), src["accepted_ts"].to_pydatetime(),
                     window=(x - timedelta(days=PRE_DAYS), x + timedelta(days=POST_DAYS)))

    def _scheduled(self, company, symbol, sym, t, day, last_report) -> Known | None:
        cal = self.calendar
        if not len(cal):
            return None
        names = {sym, symbol.partition("@")[0]}
        if company is not None:
            names.add(company.split("#")[0])
        seen = cal[(cal["snapshot_ts"] <= t) & cal["symbol"].isin(names)]
        if not len(seen):
            return None
        snap = seen[seen["snapshot_ts"] == seen["snapshot_ts"].max()]
        d = pd.to_datetime(snap["date"]).dt.date
        ok = d >= day
        if last_report is not None:
            ok &= d > last_report + timedelta(days=REPORT_GAP_DAYS)
        snap = snap[ok.to_numpy()]
        if not len(snap):
            return None
        r = snap.assign(_d=pd.to_datetime(snap["date"]).dt.date).sort_values("_d").iloc[0]
        dd, hour = r["_d"], str(r.get("hour") or "")
        if hour == "bmo":
            s = _session_on_or_after(dd)
            lcb = _prev_session(s)
        elif hour == "amc" and nyse.is_session(dd):
            s, lcb = _session_on_or_after(dd + timedelta(days=1)), dd
        else:  # during market hours or unknown: be out the night before the day
            s = _session_on_or_after(dd + timedelta(days=1))
            lcb = _prev_session(_session_on_or_after(dd))
        return Known(str(r["symbol"]), company or str(r["symbol"]), "scheduled", s, lcb,
                     r["snapshot_ts"].to_pydatetime(), hour=hour)


# ---------------------------------------------------------------- coverage against replay/earnings.py


def _idx(dates: np.ndarray, days) -> np.ndarray:
    return np.searchsorted(dates, np.asarray(list(days), dtype="datetime64[D]"), side="left")


def coverage(table: pd.DataFrame, dates: Sequence, blackout: Callable,
             headline_events: Mapping[str, Sequence[int]] | None = None,
             news_symbol: Mapping[str, str] | None = None) -> dict:
    """Numbers only, no P&L. Truth = the SEC report rows (`report`). For each company:

    - sec_estimate_flat: share of reports (after the first) for which the window around the SEC-anchored estimate
      (replay/earnings.blackout fed with SEC events) had the account flat after `last_close_before`;
    - headline_*: the same for the headline detector of replay/earnings.py (#113), measured against SEC truth, plus
      the share of SEC reports it detected within one session and the share of its detections that are SEC reports.
    `blackout(dates, events) -> (hold, entry)` is the research harness's window function (replay/earnings.py,
    passed in by the caller so this data package does not depend on the research code);
    `headline_events` maps a news symbol to its event session indexes (event_sessions of replay/earnings.py);
    `news_symbol` maps company -> news symbol (default: the company id)."""
    dates = np.asarray(dates, dtype="datetime64[D]")
    rep = table[table["report"].astype(bool)]
    out_rows = []
    per_year: dict[int, int] = {}
    for c, g in rep.groupby("company"):
        s_idx = _idx(dates, g["session"])
        l_idx = _idx(dates, g["last_close_before"])
        valid = (s_idx < len(dates)) & (l_idx < len(dates))
        s_idx, l_idx = s_idx[valid], l_idx[valid]
        for d in g["session"]:
            per_year[d.year] = per_year.get(d.year, 0) + 1
        if len(s_idx) < 2:
            continue
        hold, _ = blackout(dates, list(s_idx))
        row = {"company": c, "reports": int(len(s_idx)),
               "sec_flat": int(sum(bool(hold[li]) for li in l_idx[1:])), "sec_tested": int(len(l_idx) - 1),
               "sec_blk": float(hold[s_idx[0]:].mean())}
        ns = (news_symbol or {}).get(c, c)
        if headline_events is not None and ns in headline_events and len(headline_events[ns]):
            h = np.asarray(sorted(int(x) for x in headline_events[ns]))
            hh, _ = blackout(dates, list(h))
            later = [i for i in range(len(s_idx)) if s_idx[i] > h[0]]
            row.update(h_tested=len(later), h_flat=int(sum(bool(hh[l_idx[i]]) for i in later)),
                       h_found=int(sum(bool(np.any(np.abs(h - si) <= 1)) for si in s_idx if si >= h[0])),
                       h_span=int(sum(1 for si in s_idx if si >= h[0])),
                       h_true=int(sum(bool(np.any(np.abs(s_idx - hi) <= 2)) for hi in h)), h_n=int(len(h)),
                       h_blk=float(hh[h[0]:].mean()))
        out_rows.append(row)
    df = pd.DataFrame(out_rows)

    def share(a, b):
        return round(float(df[a].sum() / df[b].sum()), 3) if len(df) and b in df and df[b].sum() else None

    res = {"companies_with_reports": int(len(df)), "reports": int(rep.shape[0]),
           "reports_per_year": dict(sorted(per_year.items())),
           "median_reports_per_company_year": None,
           "sec_estimate": {"flat_before_report_share": share("sec_flat", "sec_tested"),
                            "reports_tested": int(df["sec_tested"].sum()) if len(df) else 0,
                            "blackout_share_of_sessions": round(float(df["sec_blk"].mean()), 3) if len(df) else None},
           "exact_dates_oracle": "100% by construction, but uses the date before it is known: an upper bound only"}
    if len(rep):
        span = rep.groupby("company")["accepted_ts"].agg(["min", "max", "count"])
        yrs = ((span["max"] - span["min"]).dt.days / 365.25).clip(lower=0.25)
        res["median_reports_per_company_year"] = round(float(((span["count"] - 1) / yrs)[yrs > 0.5].median()), 2) \
            if (yrs > 0.5).any() else None
    if headline_events is not None and len(df) and "h_n" in df:
        hd = df.dropna(subset=["h_n"])
        res["headline_estimate"] = {
            "companies": int(len(hd)),
            "sec_reports_detected_within_1_session": share("h_found", "h_span"),
            "detections_that_are_sec_reports": share("h_true", "h_n"),
            "flat_before_report_share": share("h_flat", "h_tested"),
            "blackout_share_of_sessions": round(float(hd["h_blk"].mean()), 3)}
    return res


# ---------------------------------------------------------------- matrices for the multi-day harness


def _columns(columns: Sequence[str], membership) -> dict[str, int]:
    return membership.key_map(columns) if membership is not None else {c: j for j, c in enumerate(columns)}


def report_events(table: pd.DataFrame, dates: Sequence, columns: Sequence[str], membership=None
                  ) -> dict[str, list[int]]:
    """{column: indexes in `dates` of the first tradable session of each SEC report}: the `events` argument of
    replay/earnings.py `calendar(dates, symbols, events)`, in place of its headline detections.

    Point in time: an event at index e is a filing accepted before session e opened, and `calendar` / `blackout`
    use at row t only events with e <= t. Columns are bar-store keys (288 tickers or SYMBOL@ASOF), mapped to
    companies by `membership.key_map` (or taken as company ids when `membership` is None); columns that are no
    company (SPY, ETFs) get no entry."""
    darr = np.asarray(dates, dtype="datetime64[D]")
    cmap = _columns(columns, membership)
    rep = table[table["report"].astype(bool)]
    out: dict[str, list[int]] = {}
    for c, g in rep.groupby("company"):
        j = cmap.get(c)
        if j is not None:
            out[columns[j]] = [int(x) for x in _idx(darr, g["session"]) if x < len(darr)]
    return out


def exact_matrix(table: pd.DataFrame, dates: Sequence, columns: Sequence[str], membership=None,
                 lead_sessions: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """LOOK-AHEAD upper bound, never a go/no-go number: (hold, entry) (sessions x columns) with `hold[t, j]` = flat
    after the close of each report's `last_close_before` and `entry[t, j]` = no entry in the `lead_sessions`
    sessions before it. It uses each report date before it is known; companies usually publish the date a few
    weeks ahead, which the forward-calendar snapshots record from Nov 2026 on."""
    darr = np.asarray(dates, dtype="datetime64[D]")
    n = len(darr)
    hold = np.zeros((n, len(columns)), dtype=bool)
    entry = np.zeros_like(hold)
    cmap = _columns(columns, membership)
    rep = table[table["report"].astype(bool)]
    for c, g in rep.groupby("company"):
        j = cmap.get(c)
        if j is None:
            continue
        lcb = np.asarray(list(g["last_close_before"]), dtype="datetime64[D]")
        li = np.searchsorted(darr, lcb)
        ok = li < n
        ok[ok] &= darr[li[ok]] == lcb[ok]  # a date outside `dates` is skipped
        for x in li[ok]:
            hold[x, j] = True
            entry[max(0, x - lead_sessions):x + 1, j] = True
    return hold, entry
