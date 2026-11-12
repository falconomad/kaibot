"""Earnings announcement dates, point-in-time, from SEC EDGAR (story S85, #125). Research data only.

  python scripts/fetch_earnings_dates.py --env .env             fetch (resumable) + build + snapshot + coverage
  python scripts/fetch_earnings_dates.py --env .env --refresh   also re-read the newest filings of every company
  python scripts/fetch_earnings_dates.py --env .env --snapshot  only today's forward-calendar snapshot (daily job)
  python scripts/fetch_earnings_dates.py --build                offline: rebuild from the cache
  python scripts/fetch_earnings_dates.py --coverage             offline: coverage report vs replay/earnings.py
  python scripts/fetch_earnings_dates.py --status               offline: what is stored

Reads SEC_USER_AGENT (and FINNHUB_API_KEY for the snapshot) from the env file; prints no key.
Writes data/earnings/: announcements.parquet, cik_map.csv, cik_misses.csv, build.json, coverage.json,
sec/ (per-CIK extracts: the resume state), calendar/snapshot=YYYY-MM-DD.parquet. See docs/ops/earnings_dates.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.engine.config import load_env_file  # noqa: E402
from kaibot.ingest import earnings_calendar as cal  # noqa: E402
from kaibot.ingest import earnings_sec as sec  # noqa: E402
from kaibot.ingest import nyse_sessions as nyse  # noqa: E402
from kaibot.ingest.earnings_pit import coverage  # noqa: E402
from kaibot.ingest.sp500_pit import Membership, read_universe  # noqa: E402

log = logging.getLogger("fetch_earnings_dates")


def _env(path: str | None) -> dict:
    import os

    load_env_file(path)
    return {k: os.environ.get(k, "") for k in ("SEC_USER_AGENT", "FINNHUB_API_KEY")}


def fetch_and_build(data: Path, ua: str, refresh: bool) -> dict:
    t0 = time.monotonic()
    get = sec.sec_getter(ua)
    root = data / "sec"
    root.mkdir(parents=True, exist_ok=True)
    m = Membership.load()
    tick_file = root / "company_tickers.json"
    tick_file.write_bytes(get(sec.TICKERS_URL))
    tickers = sec.ticker_ciks(json.loads(tick_file.read_bytes()))
    cache = sec.Cache(root / "submissions", get)
    done: set[str] = set()

    def fetch(cik: str):
        co = cache.company(cik, refresh=refresh and cik not in done)
        if cik not in done:
            done.add(cik)
            if len(done) % 25 == 0:
                print(f"  {len(done)} CIKs read, {cache.requests} requests this run, "
                      f"{time.monotonic() - t0:.0f} s", flush=True)
        return co

    def lookup():
        p = root / "cik-lookup-data.txt"
        if not p.is_file():
            print("Downloading EDGAR's CIK/name list (once) for companies that no longer trade…", flush=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(get(sec.CIK_LOOKUP_URL))
            tmp.replace(p)
        return sec.parse_cik_lookup(p.read_text(errors="replace"))

    print(f"{len(m.spells)} companies in the S&P 500 since 2016; reading their 8-K filings from EDGAR (5 req/s)…",
          flush=True)
    mapping = sec.resolve(m, tickers, sec.read_overrides(), fetch, sec.company_names(m), lookup)
    meta = _build(data, m, mapping, cache)
    meta.update(requests_this_run=cache.requests, seconds=round(time.monotonic() - t0),
                errors=cache.errors, refreshed=refresh)
    (data / "build.json").write_text(json.dumps(meta, indent=1, default=str))
    return meta


def _build(data: Path, m: Membership, mapping, cache: sec.Cache) -> dict:
    sec.write_mapping(mapping, data / "cik_map.csv")
    sec.write_mapping([r for r in mapping if not r.cik], data / "cik_misses.csv")
    df = sec.build(m, mapping, cache.load)
    tmp = data / "announcements.tmp"
    df.to_parquet(tmp, index=False)
    tmp.replace(data / "announcements.parquet")
    hows: dict[str, int] = {}
    for r in mapping:
        hows[r.how] = hows.get(r.how, 0) + 1
    current = m.current()
    misses = [r.company for r in mapping if not r.cik]
    return {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "companies": len(mapping),
            "mapped": sum(1 for r in mapping if r.cik), "mapped_by": hows,
            "unverified": [r.company for r in mapping if r.cik and not r.verified],
            "misses": len(misses),
            "mapped_without_rows": sorted({r.company for r in mapping if r.cik} - set(df["company"])),
            "misses_current_members": sorted(set(misses) & current),
            "rows": int(len(df)), "reports": int(df["report"].sum()) if len(df) else 0,
            "companies_with_rows": int(df["company"].nunique()) if len(df) else 0, "tz_check": sec.tz_check(df)}


def offline_build(data: Path) -> dict:
    import csv

    m = Membership.load()
    cache = sec.Cache(data / "sec" / "submissions", None)
    rows = list(csv.DictReader((data / "cik_map.csv").open()))
    mapping = [sec.Mapping_(r["company"], r["cik"], r["how"], r["verified"] == "1", r["note"]) for r in rows]
    meta = _build(data, m, mapping, cache)
    (data / "build.json").write_text(json.dumps(meta, indent=1, default=str))
    return meta


def run_snapshot(data: Path, token: str, force: bool = False) -> str:
    today = datetime.now(nyse.ET).date()
    p = cal.snapshot_path(data / "calendar", today)
    if p.is_file() and not force:
        return f"calendar snapshot for {today} already stored"
    if not token:
        return "no FINNHUB_API_KEY in the env file: no calendar snapshot (see docs/ops/earnings_dates.md)"
    m = Membership.load()
    syms = {t for c in m.current() for t in [c.split("#")[0], *m.spells[c][-1].tickers]} | set(read_universe())
    try:
        df = cal.snapshot(cal.finnhub_getter(token), today, syms)
    except cal.Unavailable as e:
        return f"forward calendar unavailable ({e}); nothing written"
    cal.write_snapshot(df, data / "calendar", today)
    return f"calendar snapshot {today}: {len(df)} rows for {df['symbol'].nunique()} symbols"


def run_coverage(data: Path) -> dict:
    import numpy as np
    import pandas as pd

    from kaibot.ingest.sp500_pit import _store_dates
    from kaibot.replay import earnings as headline

    table = pd.read_parquet(data / "announcements.parquet")
    today = datetime.now(nyse.ET).date()
    sess = nyse.sessions(sec.START, today)
    dates = np.asarray(sess, dtype="datetime64[D]")
    news_dir = data.parent
    events, ns = {}, {}
    have_news = (news_dir / "news").is_dir()
    if have_news:
        m = Membership.load()
        uni = set(read_universe())
        for c in table["company"].unique():
            sym = m.universe_ticker(c) or c.split("#")[0]
            if sym in uni and (news_dir / "news" / f"symbol={sym}").is_dir():
                events[sym] = headline.event_sessions(headline.load_headlines(news_dir, sym), dates)
                ns[c] = sym
    res = coverage(table, dates, headline.blackout, events if have_news else None, ns)
    res["headline_part"] = "computed" if have_news else "skipped: no data/news on this machine"
    spy = _store_dates(news_dir / "daily", "SPY")
    if spy:
        lo, hi = max(spy[0], sess[0]), min(spy[-1], sess[-1])
        a = {d for d in sess if lo <= d <= hi}
        b = {d for d in spy if lo <= d <= hi}
        res["nyse_calendar_vs_store"] = {"only_rules": sorted(map(str, a - b)), "only_store": sorted(map(str, b - a))}
    res["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (data / "coverage.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def status(data: Path) -> None:
    sub = data / "sec" / "submissions"
    print(f"cached CIK extracts: {len(list(sub.glob('CIK*.json'))) if sub.is_dir() else 0}")
    for f in ("build.json", "coverage.json"):
        p = data / f
        print(f"{f}: " + (p.read_text()[:1500] if p.is_file() else "not yet"))
    snaps = sorted((data / "calendar").glob("snapshot=*.parquet"))
    print(f"calendar snapshots: {len(snaps)}" + (f" (last {snaps[-1].stem.split('=')[1]})" if snaps else ""))


def _summary(meta: dict, cov: dict | None) -> None:
    print(f"\nCompanies {meta['companies']}: mapped to a CIK {meta['mapped']} {meta['mapped_by']}; "
          f"misses {meta['misses']} (data/earnings/cik_misses.csv)"
          + (f"; current members missed: {meta['misses_current_members']}" if meta["misses_current_members"] else ""))
    print(f"8-K Item 2.02 filings since 2016: {meta['rows']} ({meta['reports']} quarterly reports) "
          f"for {meta['companies_with_rows']} companies -> data/earnings/announcements.parquet")
    print(f"acceptance-time check: {meta['tz_check']}")
    if cov:
        print(f"coverage: {json.dumps({k: cov[k] for k in cov if k not in ('reports_per_year',)}, default=str)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env")
    ap.add_argument("--data", default=str(ROOT / "data" / "earnings"))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--snapshot", action="store_true")
    g.add_argument("--build", action="store_true")
    g.add_argument("--coverage", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--force", action="store_true", help="--snapshot: replace today's snapshot")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    data = Path(a.data)
    data.mkdir(parents=True, exist_ok=True)
    if a.status:
        status(data)
        return 0
    if a.build:
        _summary(offline_build(data), None)
        return 0
    if a.coverage:
        print(json.dumps(run_coverage(data), indent=1, default=str))
        return 0
    env = _env(a.env)
    if a.snapshot:
        print(run_snapshot(data, env["FINNHUB_API_KEY"], a.force))
        return 0
    if not env["SEC_USER_AGENT"]:
        print("Missing SEC_USER_AGENT in the env file ('Name email@domain')")
        return 1
    meta = fetch_and_build(data, env["SEC_USER_AGENT"], a.refresh)
    print(run_snapshot(data, env["FINNHUB_API_KEY"]))
    cov = run_coverage(data)
    _summary(meta, cov)
    return 1 if meta.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
