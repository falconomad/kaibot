"""Spin-off share ratios from the parents' SEC 8-K filings (story #298). Shadow / research data only.

  python scripts/fetch_spinoffs.py --env .env              fetch (resumable) + build, seeds: the daily store's spin-offs
  python scripts/fetch_spinoffs.py --env .env --check262   the same for the spin-offs named in #262, then the check
  python scripts/fetch_spinoffs.py --env .env --events f.csv   seeds from a CSV: parent,ex_date[,new_symbol][,cik]
  python scripts/fetch_spinoffs.py --build [--check262]    offline: rebuild from the cache
  python scripts/fetch_spinoffs.py --status                offline: what is stored

Reads only SEC_USER_AGENT from the env file; prints no key. Every request runs under the `sec_edgar` budget.
Writes data/spinoffs/: spinoffs.parquet (one row per spin-off), misses.csv, build.json, check_262.csv (--check262),
sec/ (the resume state). Nothing in the live path reads any of it. See docs/ops/spinoff_ratios.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.engine.config import load_env_file  # noqa: E402
from kaibot.ingest import spinoff_sec as sp  # noqa: E402

log = logging.getLogger("fetch_spinoffs")


def store_events(daily: Path) -> list[sp.Event]:
    """The spin-off rows of the daily store's corporate-actions table ([] when this machine has no store)."""
    from kaibot.ingest.daily_store import DailyStore

    try:
        return sp.events_from_actions(DailyStore(daily).actions())
    except FileNotFoundError:
        return []


def seeds(daily: Path, events_csv: str | None, check262: bool) -> list[sp.Event]:
    """Which spin-offs to look up. A CSV line (or a #262 line) takes its new symbol and Alpaca's ratio from the
    store's row for the same parent and ex-date, when the store has one."""
    from_store = store_events(daily)
    listed = sp.read_events(Path(events_csv)) if events_csv else sp.read_events(sp.CHECK_262) if check262 else None
    if listed is None:
        return from_store
    by: dict[tuple[str, date], list[sp.Event]] = {}
    for e in from_store:
        by.setdefault((e.parent, e.ex_date), []).append(e)
    out = []
    for e in listed:
        hits = by.get((e.parent, e.ex_date), [])
        if e.new_symbol or not hits:
            hit = next((h for h in hits if h.new_symbol == e.new_symbol), None)
            out.append(sp.Event(e.parent, e.ex_date, e.new_symbol, hit.alpaca_ratio if hit else None, e.cik))
        else:
            out += [sp.Event(h.parent, h.ex_date, h.new_symbol, h.alpaca_ratio, e.cik) for h in hits]
    return out


def _write(data: Path, events: list[sp.Event], rows: list[dict], misses: list[dict], cache: sp.Cache,
           extra: dict) -> dict:
    df = sp.table(rows)
    tmp = data / "spinoffs.tmp"
    df.to_parquet(tmp, index=False)
    tmp.replace(data / "spinoffs.parquet")
    with (data / "misses.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["parent", "new_symbol", "ex_date", "reason"], lineterminator="\n")
        w.writeheader()
        w.writerows(misses)
    both = df[df["alpaca_ratio"].notna()]
    same = ((both["ratio"] - both["alpaca_ratio"]).abs() <= 1e-6 * both["alpaca_ratio"].clip(lower=1.0))
    meta = {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "events": len(events), "rows": int(len(df)),
            "misses": len(misses), "known_before_ex": int(df["known_before_ex"].sum()) if len(df) else 0,
            "with_value": int(df["value_per_parent_share"].notna().sum()),
            "with_alpaca_ratio": int(len(both)), "ratio_equals_alpaca": int(same.sum()),
            "ratio_differs_from_alpaca": [f"{r.parent} {r.ex_date}: SEC {r.ratio:g}, Alpaca {r.alpaca_ratio:g}"
                                          for r in both[~same].itertuples()],
            "requests_this_run": cache.requests, "errors": cache.errors, **extra}
    (data / "build.json").write_text(json.dumps(meta, indent=1, default=str))
    return meta


def fetch_and_build(data: Path, ua: str, events: list[sp.Event], refresh: bool = False) -> dict:
    t0 = time.monotonic()
    get = sp.sec_getter(ua)
    cache = sp.Cache(data / "sec", get)
    tick_file = data / "sec" / "company_tickers.json"
    try:
        body = get(sp.TICKERS_URL)
        cache.requests += 1
        tick_file.write_bytes(body)
    except Exception as e:  # noqa: BLE001  (the committed CIKs of a CSV still work without today's ticker table)
        cache.errors["company_tickers"] = f"{type(e).__name__}: {str(e)[:160]}"
    tickers = sp.ticker_table(json.loads(tick_file.read_bytes())) if tick_file.is_file() else {}
    print(f"{len(events)} spin-offs to look up in the parents' 8-K filings (SEC EDGAR, 5 requests/s)…", flush=True)
    rows, misses = sp.collect(events, cache, tickers, refresh)
    return _write(data, events, rows, misses, cache, {"seconds": round(time.monotonic() - t0), "refreshed": refresh})


def offline_build(data: Path, events: list[sp.Event]) -> dict:
    cache = sp.Cache(data / "sec", None)
    tick_file = data / "sec" / "company_tickers.json"
    tickers = sp.ticker_table(json.loads(tick_file.read_bytes())) if tick_file.is_file() else {}
    rows, misses = sp.collect(events, cache, tickers)
    return _write(data, events, rows, misses, cache, {"offline": True})


def run_check(data: Path, daily: Path) -> list[dict]:
    """The #262 check: per named spin-off, found or not, the SEC ratio against Alpaca's record, and (when the daily
    store can price both companies on the ex-date) the factor the ratio implies against Alpaca's close factor.
    Alpaca's close factor is read from data/daily/check/adjustment_steps.csv when it is there, else from #262."""
    import pandas as pd

    from kaibot.ingest.daily_store import DailyStore

    df = pd.read_parquet(data / "spinoffs.parquet")
    with sp.CHECK_262.open(newline="") as fh:
        ref = list(csv.DictReader(fh))
    steps = daily / "check" / "adjustment_steps.csv"
    if steps.is_file():
        st = pd.read_csv(steps)
        measured = {(str(k).split("@")[0], str(d)[:10]): float(f)
                    for k, d, f in zip(st["key"], st["date"], st["alpaca_factor"], strict=True)}
        for r in ref:
            if (r["parent"], r["ex_date"]) in measured:
                r["alpaca_close_factor"] = measured[(r["parent"], r["ex_date"])]
    closes = None
    try:
        store = DailyStore(daily)
        keys = store.keys()
    except (FileNotFoundError, OSError):
        keys = []
    if keys:
        def closes(symbol: str, day: date) -> float:
            key = symbol if symbol in keys else next((k for k in sorted(keys) if k.split("@")[0] == symbol), None)
            if key is None:
                return math.nan
            b = store.bars(key, start=day, end=day)
            return float(b["c"].iloc[0]) if len(b) else math.nan
    lines = sp.compare(df.to_dict("records"), ref, closes)
    pd.DataFrame(lines).to_csv(data / "check_262.csv", index=False)
    return lines


def _print_check(lines: list[dict]) -> None:
    found = [x for x in lines if x["found"]]
    priced = [x for x in found if x["agrees"] is not None]
    rated = [x for x in found if x["ratio_agrees"] is not None]
    print(f"\n#262 check: {len(found)} of {len(lines)} spin-offs have a ratio from an SEC filing "
          f"({sum(1 for x in found if x['known_before_ex'])} known before the ex-date opened).")
    print(f"  against Alpaca's record ratio: {sum(1 for x in rated if x['ratio_agrees'])} of {len(rated)} equal"
          + ("" if rated else " (the store has no Alpaca ratio for these: run scripts/fetch_daily.sh first)"))
    print(f"  against Alpaca's close factor: {sum(1 for x in priced if x['agrees'])} of {len(priced)} within 0.02"
          + ("" if priced else " (needs data/daily with both companies' bars)"))
    for x in lines:
        print(f"  {x['parent']:6} {x['ex_date']}  " + (
            f"SEC {x['sec_ratio']:.6g}  Alpaca ratio {x['alpaca_ratio']:.6g}  factor Alpaca "
            f"{x['alpaca_close_factor']:.3f} / SEC-implied {x['sec_implied_factor']:.3f}  {x['filing_url']}"
            if x["found"] else "not found (see misses.csv)"))
    print("Details: data/spinoffs/check_262.csv. Please send it with build.json and misses.csv to the Data team.")


def status(data: Path) -> None:
    for sub in ("submissions", "filings"):
        d = data / "sec" / sub
        print(f"cached {sub}: {len(list(d.glob('*.json'))) if d.is_dir() else 0}")
    p = data / "build.json"
    print("build.json: " + (p.read_text()[:1500] if p.is_file() else "not yet"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env")
    ap.add_argument("--data", default=str(ROOT / "data" / "spinoffs"))
    ap.add_argument("--daily", default=str(ROOT / "data" / "daily"), help="the daily store (seeds and the check)")
    ap.add_argument("--events", help="CSV of spin-offs to look up: parent,ex_date[,new_symbol][,cik]")
    ap.add_argument("--check262", action="store_true", help="look up the spin-offs named in #262 and compare")
    ap.add_argument("--build", action="store_true", help="offline: rebuild from the cache")
    ap.add_argument("--refresh", action="store_true", help="re-read the parents' filing lists")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    data, daily = Path(a.data), Path(a.daily)
    data.mkdir(parents=True, exist_ok=True)
    if a.status:
        status(data)
        return 0
    events = seeds(daily, a.events, a.check262)
    if not events:
        print("No spin-offs to look up: no data/daily corporate-actions table here and no --events / --check262.")
        return 1
    if a.build:
        meta = offline_build(data, events)
    else:
        load_env_file(a.env)
        ua = os.environ.get("SEC_USER_AGENT", "")
        if not ua:
            print("Missing SEC_USER_AGENT in the env file ('Name email@domain')")
            return 1
        meta = fetch_and_build(data, ua, events, a.refresh)
    print(f"Spin-offs {meta['events']}: {meta['rows']} with a ratio from an SEC filing "
          f"({meta['known_before_ex']} known before the ex-date opened, {meta['with_value']} with a stated value); "
          f"{meta['misses']} misses (data/spinoffs/misses.csv). Requests this run: {meta['requests_this_run']}.")
    if meta["with_alpaca_ratio"]:
        print(f"Against Alpaca's record: {meta['ratio_equals_alpaca']} of {meta['with_alpaca_ratio']} ratios equal"
              + (f"; differ: {meta['ratio_differs_from_alpaca']}" if meta["ratio_differs_from_alpaca"] else ""))
    if a.check262:
        _print_check(run_check(data, daily))
    return 1 if meta.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
