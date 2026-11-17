"""Integrity of the daily bars themselves (issue #169; the store is story S84, #124). Offline and read-only.

`kaibot.ingest.daily_check` compares the store with other sources (the minute history, Alpaca's adjusted bars).
This module checks each stored series on its own, so a bad bar is found before it reaches the multi-day harness:

errors (the bar cannot be used as it is)
    bad_price        open, high, low or close missing, not finite, or not above zero
    ohlc_order       high below the open, close or low, or low above the open, close or high
    bad_volume       volume missing, not finite or negative
    duplicate_date   the same session twice in one file
    unsorted         dates not in increasing order
    not_a_session    a bar on a day that is not an NYSE regular session (`kaibot.ingest.nyse_sessions`)
    early_available  `available_ts` missing, or earlier than 15 minutes after that session's close: a reader
                     keyed on `available_ts <= decision_ts` could see the bar before it was complete (point in time)
warnings (worth a look, often real)
    missing_sessions NYSE sessions inside the key's first..last span with no bar (one row per run; a halt, or a
                     hole in the download)
    stale            STALE_RUN or more consecutive flat bars (open = high = low = close, unchanged from the bar
                     before): a halt or a stale feed (one row per run)
    jump             close-to-close move beyond JUMP (up or down) with no split, stock dividend or spin-off of
                     that key landing on the bar (a missing corporate action, or a bad print)
    zero_volume      a bar with no shares traded
    off_stamp        `available_ts` is not the store's own stamp for that session (see below), but not early
                     enough to be an error: a bar written by another tool, or a stamp rule that has drifted

The point-in-time reference is the stamp the store itself writes: `daily_bars.bars_table` sets `available_ts` to
`COMPLETE_AT` (20:15 ET) of the session date, because extended hours add to a day's volume until 20:00 ET; the live
daily path (`engine/live_daily.py`) and the multi-day harness treat a bar as complete from that time. The hard floor
is the session's close plus 15 minutes (16:15 ET, 13:15 ET on early-close days; SIP is free only when 15 minutes
old): a stamp before it is an error, a stamp at or after it that differs from 20:15 ET is `off_stamp`. A store
written by `bars_table` therefore never trips either check.

Outputs under <data>/daily/check: integrity.csv (every finding), integrity_summary.csv (one verdict per key:
clean, warn or error), integrity.txt (plain words).

The live daily path (engine/live_daily.StoreLoader, only with KAIBOT_MULTIDAY=on) asks `StoreGate` before each
evening's decision, on the keys that view loads only, over its look-back: see `live_verdict` for what drops one
stock and what stops every decision, and docs/ops/daily_bars.md for what to do. Warnings never block.

Exit codes of `scripts/fetch_daily.py --check` (and so of the nightly refresh, scripts/fetch_daily.sh): 0 when no
error lies inside the live window, 3 when one does, 1 when there is no store. The live window is the live daily
path's look-back (`LIVE_WINDOW_DAYS`, engine/live_daily.StoreLoader.LOOKBACK_DAYS) before the store's last session:
an error older than that is never read by the engine, so it is listed as an old finding (with a count) and does not
make the nightly job report FAILED.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.ingest import nyse_sessions
from kaibot.ingest.alpaca_history import ET
from kaibot.ingest.daily_bars import COMPLETE_AT, SHARE_TYPES, bars_path
from kaibot.ingest.daily_store import DailyStore

LIVE_WINDOW_DAYS = 600  # = engine/live_daily.StoreLoader.LOOKBACK_DAYS (a test pins it)
JUMP = 0.5  # |close / previous close - 1| above this is a jump
STALE_RUN = 5  # this many flat, unchanged bars in a row
AVAILABLE_AFTER = timedelta(minutes=15)  # SIP history is free and complete only once it is 15 minutes old
REL_TOL = 1e-9  # float noise allowed in the high / low ordering
EXPLAINS_JUMP = (*SHARE_TYPES, "spin_off")
ERRORS = ("bad_price", "ohlc_order", "bad_volume", "duplicate_date", "unsorted", "not_a_session", "early_available")
WARNINGS = ("missing_sessions", "stale", "jump", "zero_volume", "off_stamp")
FINDING_COLUMNS = ["key", "date", "severity", "kind", "detail"]
SUMMARY_COLUMNS = ["key", "verdict", "bars", "first", "last", "errors", "warnings", *ERRORS, *WARNINGS]


@cache
def _due(d: date) -> np.datetime64:
    """Earliest allowed `available_ts` of session `d` (naive UTC, seconds): its close plus AVAILABLE_AFTER. Cached,
    since every key of the store asks for the same few thousand sessions."""
    return np.datetime64((nyse_sessions.close_utc(d) + AVAILABLE_AFTER).replace(tzinfo=None), "s")


@cache
def _stamp(d: date) -> np.datetime64:
    """The `available_ts` the store itself writes for session `d` (naive UTC, seconds): COMPLETE_AT ET that day."""
    return np.datetime64(datetime.combine(d, COMPLETE_AT, ET).astimezone(UTC).replace(tzinfo=None), "s")


def _finding(key: str, day, kind: str, detail: str = "") -> dict:
    return {"key": key, "date": str(np.datetime64(day, "D")), "severity": "error" if kind in ERRORS else "warn",
            "kind": kind, "detail": detail}


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) index pairs, end inclusive, of each run of True in `mask`."""
    m = np.concatenate([[False], np.asarray(mask, dtype=bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1, strict=True))


def _num(df: pd.DataFrame, col: str) -> np.ndarray:
    return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float) if col in df else np.full(len(df), np.nan)


def check_bars(key: str, bars: pd.DataFrame, actions: pd.DataFrame | None = None, *,
               calendar: np.ndarray | None = None, due: Callable[[date], np.datetime64] | None = None) -> list[dict]:
    """Every finding for one key's raw bars (columns of `DailyStore.bars`). `actions` are that key's corporate
    actions (columns type, ex_date); without them every large move is reported as a jump. `calendar` (sorted
    datetime64[D] sessions) replaces the NYSE rule calendar and `due(d)` the earliest allowed `available_ts` of
    session d (default: its close plus 15 minutes); the live gate passes SPY's sessions and the store's own stamp."""
    out: list[dict] = []
    if not len(bars):
        return out
    dates = np.asarray(bars["date"]).astype("datetime64[D]")
    o, h, lo, c, v = (_num(bars, k) for k in ("o", "h", "l", "c", "v"))

    # --- order and uniqueness of dates (reported on the raw order, then checked on the sorted series)
    back = np.flatnonzero(np.diff(dates) < np.timedelta64(0, "D"))
    for i in back:
        out.append(_finding(key, dates[i + 1], "unsorted", f"after {dates[i]}"))
    order = np.argsort(dates, kind="stable")
    dates, o, h, lo, c, v = dates[order], o[order], h[order], lo[order], c[order], v[order]
    dup = np.flatnonzero(np.diff(dates) == np.timedelta64(0, "D"))
    for i in dup:
        out.append(_finding(key, dates[i + 1], "duplicate_date"))

    # --- prices and volume
    px = np.vstack([o, h, lo, c])
    bad_px = ~(np.isfinite(px) & (px > 0)).all(axis=0)
    for i in np.flatnonzero(bad_px):
        out.append(_finding(key, dates[i], "bad_price", f"o={o[i]} h={h[i]} l={lo[i]} c={c[i]}"))
    ok = ~bad_px
    top, bottom = np.fmax(np.fmax(o, c), lo), np.fmin(np.fmin(o, c), h)
    wrong = ok & ((h < top * (1 - REL_TOL)) | (lo > bottom * (1 + REL_TOL)))
    for i in np.flatnonzero(wrong):
        out.append(_finding(key, dates[i], "ohlc_order", f"o={o[i]} h={h[i]} l={lo[i]} c={c[i]}"))
    for i in np.flatnonzero(~(np.isfinite(v) & (v >= 0))):
        out.append(_finding(key, dates[i], "bad_volume", f"v={v[i]}"))
    for i in np.flatnonzero(v == 0):
        out.append(_finding(key, dates[i], "zero_volume"))

    # --- the NYSE calendar
    first, last = dates[0].astype(object), dates[-1].astype(object)
    if calendar is None:
        cal = np.array(nyse_sessions.sessions(first, last), dtype="datetime64[D]")
    else:
        cal = np.asarray(calendar, dtype="datetime64[D]")
        cal = cal[(cal >= dates[0]) & (cal <= dates[-1])]
    for i in np.flatnonzero(~np.isin(dates, cal)):
        out.append(_finding(key, dates[i], "not_a_session", f"weekday {dates[i].astype(object):%a}"))
    missing = ~np.isin(cal, dates)
    for a, b in _runs(missing):
        n = b - a + 1
        out.append(_finding(key, cal[a], "missing_sessions",
                            f"{n} session{'s' if n > 1 else ''} through {cal[b]}" if n > 1 else "1 session"))

    # --- point in time: a bar must not be stamped as known before its session was over
    if "available_ts" in bars and len(cal):
        ts = (pd.to_datetime(bars["available_ts"], utc=True).dt.tz_convert(None).to_numpy()
              .astype("datetime64[s]")[order])  # UTC, NaT where missing
        on_cal = np.isin(dates, cal)  # off-calendar bars are already errors above: no close to compare with
        cal_o = cal.astype(object)
        floor = np.array([(due or _due)(d) for d in cal_o], dtype="datetime64[s]")
        stamp = np.array([_stamp(d) for d in cal_o], dtype="datetime64[s]")
        j = np.clip(np.searchsorted(cal, dates), 0, len(cal) - 1)
        early = on_cal & (np.isnat(ts) | (ts < floor[j]))
        off = on_cal & ~early & (ts != stamp[j])
        for i in np.flatnonzero(early | off):
            if early[i]:
                out.append(_finding(key, dates[i], "early_available",
                                    f"available_ts {ts[i]} is before {floor[j[i]]} UTC"))
            else:
                out.append(_finding(key, dates[i], "off_stamp",
                                    f"available_ts {ts[i]} UTC, the store writes {stamp[j[i]]} UTC"))

    # --- flat, unchanged runs
    flat = ok & (o == h) & (h == lo) & (lo == c)
    same = np.zeros(len(c), dtype=bool)
    same[1:] = flat[1:] & (c[1:] == c[:-1])
    if len(same):
        same[0] = False
    for a, b in _runs(same):
        a = a - 1 if a > 0 and flat[a - 1] else a  # the run starts at the first flat bar of the price
        if b - a + 1 >= STALE_RUN:
            out.append(_finding(key, dates[a], "stale", f"{b - a + 1} flat bars at {c[a]} through {dates[b]}"))

    # --- large moves no corporate action explains
    explained = set()
    if actions is not None and len(actions):
        acts = actions[actions["type"].isin(EXPLAINS_JUMP)]
        for ex in np.asarray(acts["ex_date"]).astype("datetime64[D]"):
            k = int(np.searchsorted(dates, ex))
            if k < len(dates):
                explained.add(k)
    good = np.flatnonzero(ok)
    for p, i in zip(good[:-1], good[1:], strict=True):
        r = c[i] / c[p] - 1.0
        if abs(r) > JUMP and not any(k in explained for k in range(p + 1, i + 1)):
            out.append(_finding(key, dates[i], "jump", f"close {c[p]} -> {c[i]} ({r * 100:+.1f}%)"))
    return out


def summarise(key: str, bars: pd.DataFrame, findings: Iterable[dict]) -> dict:
    fs = list(findings)
    row = {"key": key, "bars": len(bars), "first": "", "last": ""}
    if len(bars):
        d = np.asarray(bars["date"]).astype("datetime64[D]")
        row["first"], row["last"] = str(d.min()), str(d.max())
    for kind in (*ERRORS, *WARNINGS):
        row[kind] = sum(f["kind"] == kind for f in fs)
    row["errors"] = sum(f["severity"] == "error" for f in fs)
    row["warnings"] = sum(f["severity"] == "warn" for f in fs)
    row["verdict"] = "error" if row["errors"] else "warn" if row["warnings"] else "clean"
    return row


def check_store(store: DailyStore, keys: Sequence[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(every finding, one verdict per key) for `keys` (default: every key in the store)."""
    keys = list(keys) if keys is not None else store.keys()
    try:
        acts = store.actions()
    except FileNotFoundError:
        acts = None
    findings, rows = [], []
    for key in keys:
        bars = store.bars(key)
        mine = acts[acts["key"] == key] if acts is not None else None
        fs = check_bars(key, bars, mine)
        findings += fs
        rows.append(summarise(key, bars, fs))
    return (pd.DataFrame(findings, columns=FINDING_COLUMNS), pd.DataFrame(rows, columns=SUMMARY_COLUMNS))


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- the live daily path's gate (#230)

STRUCTURAL = ("duplicate_date", "unsorted", "early_available")  # the series itself cannot be trusted: no decision
MAX_DROPPED_SHARE = 0.01  # more than this share of the view's stocks failing (at least one is allowed): no decision


@dataclass(frozen=True)
class LiveVerdict:
    """The gate's answer for one evening's view. `block`: why no rule may decide (None: they may). `dropped`: stocks
    with a bad bar that leave this evening's view (key -> plain reason); a rule holding one of them does not decide
    (`engine/live_daily`), because the "not in the view" path would sell it."""

    block: str | None = None
    dropped: Mapping[str, str] = field(default_factory=dict)


def failure_reason(errors: pd.DataFrame, show: int = 3) -> str | None:
    """None when there is no error; else one short line, e.g. "2 bad bars in 1 key: AAA 2026-11-13 ohlc_order,
    AAA 2026-11-12 bad_price" (at most `show` named, newest first, then ", ...")."""
    if errors is None or not len(errors):
        return None
    e = errors.sort_values(["date", "key"], ascending=[False, True])
    n, k = len(e), e["key"].nunique()
    named = [f"{r.key} {r.date} {r.kind}" for r in e.head(show).itertuples()]
    return (f"{n} bad bar{'s' if n > 1 else ''} in {k} key{'s' if k > 1 else ''}: " + ", ".join(named)
            + (", ..." if n > show else ""))[:200]


def window_errors(store: DailyStore, start, end, keys: Sequence[str], market: str = "SPY") -> pd.DataFrame:
    """The hard errors on bars dated start..end of `keys`, the live way: the session calendar is `market`'s own
    bars (SPY, the calendar the engine's daily view uses; SPY itself is not checked against a calendar) and a bar
    must be stamped no earlier than the store's own `available_ts` (COMPLETE_AT ET of its date), which is the time
    the live path relies on. No rule-based calendar (nyse_sessions is research-only). Raises when a file is
    unreadable."""
    spy = store.bars(market, start=start, end=end)
    cal = np.unique(np.asarray(spy["date"]).astype("datetime64[D]"))
    found = []
    for key in keys:
        bars = store.bars(key, start=start, end=end)
        fs = check_bars(key, bars, calendar=(np.asarray(bars["date"]).astype("datetime64[D]") if key == market
                                             else cal), due=_stamp)
        found += [f for f in fs if f["severity"] == "error"]
    return pd.DataFrame(found, columns=FINDING_COLUMNS)


def live_verdict(errors: pd.DataFrame, stocks: Sequence[str]) -> LiveVerdict:
    """Sort the window's errors (`window_errors`) into the gate's answer. No decision for anyone when an error is
    structural (STRUCTURAL), sits in a key that is not one of the view's `stocks` (SPY, the sector ETFs), or hits
    more than MAX_DROPPED_SHARE of the stocks (at least one stock may fail). Otherwise each failing stock is
    dropped from this evening's view."""
    if not len(errors):
        return LiveVerdict()
    stock = set(stocks)
    hard = errors[errors["kind"].isin(STRUCTURAL) | ~errors["key"].isin(stock)]
    if len(hard):
        return LiveVerdict(block=failure_reason(hard))
    bad = sorted(errors["key"].unique())
    allowed = max(1, int(MAX_DROPPED_SHARE * len(stock)))
    if len(bad) > allowed:
        return LiveVerdict(block=f"{len(bad)} of {len(stock)} stocks fail the check (more than {allowed}): "
                                 + (failure_reason(errors) or "")[:150])
    return LiveVerdict(dropped={k: failure_reason(errors[errors["key"] == k]) or k for k in bad})


class StoreGate:
    """The live daily path's gate on the store under `root` (<data>/daily): `gate(start, end, keys, stocks)` is the
    `LiveVerdict` for the bars of the keys the view loads, dated start..end. Cached until one of those files
    changes (name, size, mtime), so an engine waiting on a bad store does not re-read it every tick. A bars file
    that cannot be read blocks too ("bars unreadable: ..."), never an exception (QA-Ops review of #234)."""

    def __init__(self, root: str | Path, market: str = "SPY") -> None:
        self.root, self.market = Path(root), market
        self._last: tuple | None = None
        self._verdict = LiveVerdict()

    def _fingerprint(self, keys: Sequence[str]) -> tuple:
        out = []
        for k in keys:
            try:
                st = bars_path(self.root, k).stat()
                out.append((k, st.st_size, st.st_mtime_ns))
            except OSError:
                out.append((k, None, None))
        return tuple(out)

    def __call__(self, start, end, keys: Sequence[str], stocks: Sequence[str]) -> LiveVerdict:
        keys = sorted(set(keys) | {self.market})
        fp = (str(start), str(end), tuple(sorted(stocks)), self._fingerprint(keys))
        if fp != self._last:
            try:
                errs = window_errors(DailyStore(self.root), start, end, keys, self.market)
                self._verdict = live_verdict(errs, stocks)
            except Exception as e:  # unreadable or missing file of a key the view needs: fail closed
                what = str(e).replace(str(self.root / "bars") + os.sep, "")
                self._verdict = LiveVerdict(block=f"bars unreadable: {type(e).__name__}: {what}"[:200])
            self._last = fp
        return self._verdict


def run_integrity(data_dir: str | Path, keys: Sequence[str] | None = None,
                  log: Callable[[str], None] = print) -> dict:
    """Check the store under <data>/daily, write the files under <data>/daily/check and a summary in plain words."""
    store = DailyStore(Path(data_dir) / "daily")
    out = store.root / "check"
    findings, summary = check_store(store, keys)
    _save(findings, out / "integrity.csv")
    _save(summary, out / "integrity_summary.csv")
    verdicts = summary["verdict"].value_counts().to_dict() if len(summary) else {}
    lines = [f"Bar integrity: {len(summary)} keys: "
             + ", ".join(f"{verdicts.get(v, 0)} {v}" for v in ("clean", "warn", "error")) + "."]
    by_kind = findings["kind"].value_counts().to_dict() if len(findings) else {}
    for kind in (*ERRORS, *WARNINGS):
        if by_kind.get(kind):
            keys_hit = sorted(findings.loc[findings["kind"] == kind, "key"].unique())
            sev = "error" if kind in ERRORS else "warning"
            lines.append(f"  {kind} ({sev}): {by_kind[kind]} in {len(keys_hit)} keys ("
                         + ", ".join(keys_hit[:10]) + (", ..." if len(keys_hit) > 10 else "") + ")")
    errs = findings[findings["severity"] == "error"] if len(findings) else findings
    last = pd.to_datetime(summary["last"], errors="coerce").max() if len(summary) else pd.NaT
    cutoff = (last.date() - timedelta(days=LIVE_WINDOW_DAYS)) if pd.notna(last) else None
    is_old = (pd.to_datetime(errs["date"]).dt.date < cutoff) if cutoff is not None and len(errs) else None
    old = errs[is_old] if is_old is not None else errs.iloc[:0]
    recent = errs[~is_old] if is_old is not None else errs
    if len(old):
        n, k = len(old), old["key"].nunique()
        lines.append(f"  Old findings: {n} error{'s' if n > 1 else ''} in {k} key{'s' if k > 1 else ''} before "
                     f"{cutoff}, outside the live daily path's {LIVE_WINDOW_DAYS}-day window (never read by the "
                     "engine; not a failure). Details in integrity.csv.")
    if len(recent):
        lines.append(f"  {len(recent)} error{'s' if len(recent) > 1 else ''} inside the live window (from {cutoff}). "
                     "Keys with errors should not be traded in a simulation until the bars are fetched again "
                     "(scripts/fetch_daily.py --refresh --symbols ...); details in integrity.csv.")
    for s in lines:
        log(s)
    out.mkdir(parents=True, exist_ok=True)
    (out / "integrity.txt").write_text("\n".join(lines) + "\n")
    return {"keys": len(summary), "verdicts": verdicts, "findings": by_kind, "errors": len(recent),
            "old_errors": len(old), "window_from": str(cutoff) if cutoff else None}
