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
    early_available  `available_ts` earlier than 15 minutes after that session's close: a reader keyed on
                     `available_ts <= decision_ts` could see the bar before it was complete (point in time)
warnings (worth a look, often real)
    missing_sessions NYSE sessions inside the key's first..last span with no bar (one row per run; a halt, or a
                     hole in the download)
    stale            STALE_RUN or more consecutive flat bars (open = high = low = close, unchanged from the bar
                     before): a halt or a stale feed (one row per run)
    jump             close-to-close move beyond JUMP (up or down) with no split, stock dividend or spin-off of
                     that key landing on the bar (a missing corporate action, or a bad print)
    zero_volume      a bar with no shares traded

Outputs under <data>/daily/check: integrity.csv (every finding), integrity_summary.csv (one verdict per key:
clean, warn or error), integrity.txt (plain words).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.ingest import nyse_sessions
from kaibot.ingest.daily_bars import SHARE_TYPES
from kaibot.ingest.daily_store import DailyStore

JUMP = 0.5  # |close / previous close - 1| above this is a jump
STALE_RUN = 5  # this many flat, unchanged bars in a row
AVAILABLE_AFTER = timedelta(minutes=15)  # SIP history is free and complete only once it is 15 minutes old
REL_TOL = 1e-9  # float noise allowed in the high / low ordering
EXPLAINS_JUMP = (*SHARE_TYPES, "spin_off")
ERRORS = ("bad_price", "ohlc_order", "bad_volume", "duplicate_date", "unsorted", "not_a_session", "early_available")
WARNINGS = ("missing_sessions", "stale", "jump", "zero_volume")
FINDING_COLUMNS = ["key", "date", "severity", "kind", "detail"]
SUMMARY_COLUMNS = ["key", "verdict", "bars", "first", "last", "errors", "warnings", *ERRORS, *WARNINGS]


@cache
def _due(d: date) -> np.datetime64:
    """Earliest allowed `available_ts` of session `d` (naive UTC, seconds): its close plus AVAILABLE_AFTER. Cached,
    since every key of the store asks for the same few thousand sessions."""
    return np.datetime64((nyse_sessions.close_utc(d) + AVAILABLE_AFTER).replace(tzinfo=None), "s")


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


def check_bars(key: str, bars: pd.DataFrame, actions: pd.DataFrame | None = None) -> list[dict]:
    """Every finding for one key's raw bars (columns of `DailyStore.bars`). `actions` are that key's corporate
    actions (columns type, ex_date); without them every large move is reported as a jump."""
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
    cal = np.array(nyse_sessions.sessions(first, last), dtype="datetime64[D]")
    for i in np.flatnonzero(~np.isin(dates, cal)):
        out.append(_finding(key, dates[i], "not_a_session", f"weekday {dates[i].astype(object):%a}"))
    missing = ~np.isin(cal, dates)
    for a, b in _runs(missing):
        n = b - a + 1
        out.append(_finding(key, cal[a], "missing_sessions",
                            f"{n} session{'s' if n > 1 else ''} through {cal[b]}" if n > 1 else "1 session"))

    # --- point in time: a bar must not be stamped as known before its session was over
    if "available_ts" in bars:
        ts = (pd.to_datetime(bars["available_ts"], utc=True).dt.tz_convert(None).to_numpy()
              .astype("datetime64[s]")[order])  # UTC, NaT where missing
        on_cal = np.isin(dates, cal)
        for i in range(len(dates)):
            d = dates[i].astype(object)
            if not on_cal[i]:
                continue  # already an error above; no close to compare with
            due = _due(d)
            if np.isnat(ts[i]) or ts[i] < due:
                out.append(_finding(key, dates[i], "early_available",
                                    f"available_ts {ts[i]} is before {due} UTC"))

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
    if any(by_kind.get(k) for k in ERRORS):
        lines.append("  Keys with errors should not be traded in a simulation until the bars are fetched again "
                     "(scripts/fetch_daily.py --refresh --symbols ...); details in integrity.csv.")
    for s in lines:
        log(s)
    out.mkdir(parents=True, exist_ok=True)
    (out / "integrity.txt").write_text("\n".join(lines) + "\n")
    return {"keys": len(summary), "verdicts": verdicts, "findings": by_kind}
