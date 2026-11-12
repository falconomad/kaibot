"""Point-in-time earnings blackout for the multi-day research (issue #113). Research only.

What the history on disk can and cannot tell us:
- data/news (Benzinga headlines with their publication time) shows that a company HAS reported: the results
  headline ("X Q3 EPS $1.20 Beats $1.15 Estimate, Sales ..."). That is known from its timestamp on, not before.
- data/fundamentals (SEC companyfacts) has the 10-Q / 10-K filing dates. They come after the results (the same
  day up to several weeks later for a 10-K), so they are not the earnings date and are not used.
- Nothing on disk says, before the day, WHEN a company will report (no earnings calendar with announcement
  times). The exact next date therefore cannot be established point-in-time.

What is done instead, using only headlines already published at each decision: the next report is expected
one year (364 days) after the same quarter's report of the year before, or 91 days after the last report while
that one is not in the news history. A position may not be held overnight from `pre_days` (10) calendar days
before that expected date until the report shows up in the news or `post_days` (10) days after it pass, and no
new position is opened in the `lead_days` (7) before that window. This is a window around an estimate, not a
known date, so `coverage` measures on the same history how often it would have had the account flat the night
before a report, and the caller uses the filter only when that is reliable (`RELIABLE`), and otherwise runs
without it and says so.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

ET = "America/New_York"
CUTOFF_MIN = 15 * 60 + 55  # a headline from 15:55 ET on cannot be acted on at that day's close
PRE_DAYS, POST_DAYS, LEAD_DAYS = 10, 10, 7
RELIABLE = {"min_symbols_share": 0.80, "min_recall": 0.85, "events_per_year": (3.0, 5.0)}

_QUARTER = r"(?:Q[1-4]|(?:First|Second|Third|Fourth|1st|2nd|3rd|4th)[- ]Quarter)"
# "X Q3 EPS $1.20 Beats $1.15 Estimate, Sales $2.1B ...", "X Reports Q3 Adj. EPS $(0.10) vs ...": a quarter, a
# metric and a dollar figure. The figure is what separates the report from stories about it.
_FIGURES = re.compile(
    rf"\b{_QUARTER}\b.{{0,40}}?\b(?:EPS|FFO|AFFO|Earnings|Net Income|Net Loss|Sales|Revenues?)\b.{{0,12}}?"
    r"\$\(?\d", re.I)
# "X Reports Third Quarter 2023 Results", "X Shares Are Trading Higher After The Company Reported ... Q2 Results"
_ANNOUNCED = re.compile(
    rf"\b(?:Reports?|Reported|Announces?|Announced|Posts?|Posted)\b.{{0,60}}?\b{_QUARTER}\b.{{0,30}}?"
    r"\b(?:Results|Earnings)\b", re.I)
_NOT_A_REPORT = re.compile(
    r"\b(?:Previews?|Ahead Of|(?<!Than[- ])Expect(?:s|ed|ing|ations)?|Scheduled|Sees|Guid(?:es|ed|ance)|Outlook|"
    r"Forecasts?|Analysts?|Price Target|Maintains|Upgrades?|Downgrades?|Raises|Lowers|Cuts|Reiterates|Initiates|"
    r"Will Report|To Report|Set To|Date For|Conference Call|Webcast|Transcript|Preannounce[sd]?|Preliminary|"
    r"Whisper|Consensus)\b", re.I)


def is_results_headline(headline: str, n_symbols: int = 1) -> bool:
    """A company's own results headline: not a preview, guidance, an analyst note or a round-up of many tickers
    (two tickers are allowed: share classes such as GOOG / GOOGL)."""
    if n_symbols > 2 or not headline:
        return False
    return bool((_FIGURES.search(headline) or _ANNOUNCED.search(headline)) and not _NOT_A_REPORT.search(headline))


def load_headlines(data_dir: Path, symbol: str) -> pd.DataFrame:
    files = sorted((Path(data_dir) / "news" / f"symbol={symbol}").glob("*.parquet"))
    if not files:
        return pd.DataFrame(columns=["created_at", "headline", "symbols"])
    df = pd.concat([pd.read_parquet(f, columns=["created_at", "headline", "symbols"]) for f in files])
    return df.drop_duplicates(["created_at", "headline"]).sort_values("created_at", ignore_index=True)


def event_sessions(news: pd.DataFrame, dates: np.ndarray, min_gap_days: int = 30) -> np.ndarray:
    """Index of the first session that can trade on each detected report: the same session when the headline is
    out before 15:55 ET, otherwise the next one. Reports closer than `min_gap_days` are one report (follow-ups)."""
    if not len(news):
        return np.array([], dtype=int)
    n_sym = news["symbols"].fillna("").map(lambda s: len([x for x in str(s).split(",") if x]))
    hit = [is_results_headline(str(h), int(n)) for h, n in zip(news["headline"], n_sym, strict=True)]
    t = pd.to_datetime(news["created_at"][hit], utc=True).dt.tz_convert(ET)
    if not len(t):
        return np.array([], dtype=int)
    day = t.dt.tz_localize(None).dt.normalize().to_numpy().astype("datetime64[D]")
    late = (t.dt.hour * 60 + t.dt.minute).to_numpy() >= CUTOFF_MIN
    idx = np.where(late, np.searchsorted(dates, day, side="right"), np.searchsorted(dates, day, side="left"))
    out: list[int] = []
    for i in sorted(int(x) for x in idx if x < len(dates)):
        if not out or (dates[i] - dates[out[-1]]).astype(int) >= min_gap_days:
            out.append(i)
    return np.array(out, dtype=int)


def blackout(dates: np.ndarray, events: Sequence[int], pre_days: int = PRE_DAYS, post_days: int = POST_DAYS,
             lead_days: int = LEAD_DAYS) -> tuple[np.ndarray, np.ndarray]:
    """(hold, entry) per session for one symbol. `hold[t]`: be flat after the close of t. `entry[t]`: open
    nothing on what is known at the close of t. Row t uses reports whose event session is <= t only."""
    day = np.asarray(dates, dtype="datetime64[D]").astype(np.int64)
    n = len(day)
    hold, entry = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    ev = [int(e) for e in events if 0 <= int(e) < n]
    known: list[int] = []
    k, x = 0, None
    for t in range(n):
        new = False
        while k < len(ev) and ev[k] <= t:
            known.append(int(day[ev[k]]))
            k, new = k + 1, True
        if not known:
            continue  # no report seen yet: nothing to anchor a window on, the symbol is not covered
        if new:
            last = known[-1]
            year_ago = [e + 364 for e in known if last + 45 < e + 364 <= last + 135]  # the next quarter's
            x = min(year_ago) if year_ago else last + 91
        while x + post_days < day[t]:  # the expected report never showed up in the news: move on a quarter
            x += 91
        nxt = day[t + 1] if t + 1 < n else day[t] + 1
        hold[t] = nxt >= x - pre_days
        entry[t] = day[t] + lead_days >= x - pre_days
    return hold, entry


def calendar(dates: np.ndarray, symbols: Sequence[str], events: Mapping[str, Sequence[int]],
             ) -> tuple[np.ndarray, np.ndarray]:
    """(hold, entry) matrices (sessions x symbols); symbols without detected reports stay all-False."""
    hold = np.zeros((len(dates), len(symbols)), dtype=bool)
    entry = np.zeros_like(hold)
    for j, s in enumerate(symbols):
        ev = events.get(s)
        if ev is not None and len(ev):
            hold[:, j], entry[:, j] = blackout(dates, ev)
    return hold, entry


def coverage(dates: np.ndarray, symbols: Sequence[str], events: Mapping[str, Sequence[int]], hold: np.ndarray,
             has_bar: np.ndarray, stocks: Sequence[str]) -> dict:
    """How far the blackout can be trusted on this history, from numbers that do not involve any P&L:
    the share of stocks whose detected reports come at a plausible rate (3 to 5 a year since the first one), the
    share of detected reports (after each stock's first) for which the account would have been flat the night
    before, and the share of stock-sessions blacked out. `reliable` applies the pre-declared `RELIABLE` bar."""
    col = {s: j for j, s in enumerate(symbols)}
    day = np.asarray(dates, dtype="datetime64[D]").astype(np.int64)
    lo_r, hi_r = RELIABLE["events_per_year"]
    plausible = with_events = tested = flat = blk = bars = 0
    rates = []
    for s in stocks:
        j = col.get(s)
        if j is None or not has_bar[:, j].any():
            continue
        rows = np.flatnonzero(has_bar[:, j])
        bars += len(rows)
        blk += int(hold[rows, j].sum())
        ev = [int(e) for e in events.get(s, ()) if 0 <= int(e) < len(day)]
        if not ev:
            continue
        with_events += 1
        years = max((day[rows[-1]] - day[ev[0]]) / 365.25, 0.25)
        if years > 0.5:  # a stock listed or first seen in the news a few months ago cannot be judged
            rate = (len(ev) - 1) / years
            rates.append(rate)
            plausible += bool(lo_r <= rate <= hi_r)
        for e in ev[1:]:
            if e >= 1:
                tested += 1
                flat += bool(hold[e - 1, j])
    n = sum(1 for s in stocks if s in col and has_bar[:, col[s]].any())
    share = plausible / n if n else 0.0
    recall = flat / tested if tested else 0.0
    return {"stocks": n, "stocks_with_reports": with_events, "stocks_plausible_share": round(share, 3),
            "reports_detected": int(sum(len(events.get(s, ())) for s in stocks)),
            "median_reports_per_year": round(float(np.median(rates)), 2) if rates else None,
            "reports_tested": tested, "flat_before_report_share": round(recall, 3),
            "blackout_share_of_sessions": round(blk / bars, 3) if bars else 0.0,
            "reliable": bool(share >= RELIABLE["min_symbols_share"] and recall >= RELIABLE["min_recall"]),
            "bar": RELIABLE, "window_days": {"pre": PRE_DAYS, "post": POST_DAYS, "lead": LEAD_DAYS}}
