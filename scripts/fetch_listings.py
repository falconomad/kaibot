"""History of new US listings from SEC EDGAR's quarterly indexes (story S102, #376). Research data only.

  python scripts/fetch_listings.py --since 2016-01-01             fetch (resumable) + build
  python scripts/fetch_listings.py --since 2016-01-01 --prices    also read each prospectus cover (see below)
  python scripts/fetch_listings.py --first-bars                   first sessions from Alpaca daily bars (below)
  python scripts/fetch_listings.py --build                        offline: rebuild from what is cached
  python scripts/fetch_listings.py --status                       offline: what is stored
  python scripts/fetch_listings.py --eligible 2026-11-19          offline: the "first eligible for" table of a
                                                                  listing whose first session was that day
  python scripts/fetch_listings.py --show ANT                     offline: what the nightly watch (collect_alt.py
                                                                  --source listings) knows about a symbol
  python scripts/fetch_listings.py --report                       offline: the last 30 nights of new Alpaca symbols
                                                                  by kind, and which were reported as new listings

One request per quarter (www.sec.gov/Archives/edgar/full-index/YYYY/QTRn/master.idx) plus the ticker table: about
45 requests since 2016, a few minutes, most of it download (each index is tens of MB before compression). A
finished quarter is cached as a small extract and never fetched again; the current quarter is read again on every
run. --prices adds one request per listing (the top 200 kB of its prospectus), a few thousand in all, once.

Reads only SEC_USER_AGENT, at run time, from the env file (--env, else KAIBOT_ENV_FILE, else
~/Projects/kaibot-secrets/.env); prints no key. Every request runs under the shared `sec_edgar` budget (5 a second).
Writes data/listings/: ipos.parquet, build.json, sec/ (the resume state). The first trading session comes from the
daily bar store (data/daily). For a listing the store does not hold, --first-bars reads its first daily bar from
Alpaca (paper keys from the env file, one request per listing of kind `ipo`, at most 40 a minute so that a running
engine keeps its room) into data/listings/alpaca_first_bars.json, never into data/daily (Product, 19 Nov 2026).
Resumable: a final answer is never asked again. Otherwise the first session stays empty. Nothing is guessed.
Three quarters in a row that fail the same way stop the run (the rest are not asked); an answer that is not an
index is named in the error (status, content type, size, its first 80 characters, redacted).
The engine reads none of it. See docs/ops/listings.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.common import http, switches  # noqa: E402  (M10 1.23: KAIBOT_* as declared there)
from kaibot.common.ratelimit import Budget, BudgetSpec  # noqa: E402
from kaibot.engine.config import load_env_file  # noqa: E402
from kaibot.ingest import listings_pit as lp  # noqa: E402
from kaibot.ingest import nyse_sessions as nyse  # noqa: E402
from kaibot.ingest.alt import listings as ls  # noqa: E402
from kaibot.ingest.earnings_sec import TICKERS_URL, sec_response_getter  # noqa: E402

DEFAULT_ENV = "~/Projects/kaibot-secrets/.env"
FULL_INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/master.idx"
FIRST_BARS_PER_MIN = 40  # the slow rate of fetch_daily.sh while the engine runs; Alpaca allows 200 a minute
MAX_ERRORS_IN_A_ROW = 5
MAX_SAME_FAILURES = 3  # quarters failing with the same error in a row: stop, do not ask the rest (#376)


def quarters(since: date, today: date) -> list[tuple[int, int]]:
    out, y, q = [], since.year, (since.month - 1) // 3 + 1
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        out.append((y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def _extract(sec: Path, y: int, q: int) -> Path:
    return sec / "full-index" / f"{y}Q{q}.json"


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _rows(sec: Path) -> list[dict]:
    rows = []
    for p in sorted((sec / "full-index").glob("*.json")):
        for r in json.loads(p.read_text()):
            rows.append({**r, "filed": date.fromisoformat(r["filed"])})
    return rows


def _covers(sec: Path) -> dict[str, dict]:
    p = sec / "prospectus_covers.json"
    covers = json.loads(p.read_text()) if p.is_file() else {}
    for c in covers.values():
        if c.get("prospectus_date"):
            c["prospectus_date"] = date.fromisoformat(c["prospectus_date"])
    return covers


def build(data: Path, daily: Path, extra: dict | None = None) -> dict:
    sec = data / "sec"
    tick = sec / "company_tickers.json"
    tickers = lp.cik_tickers(json.loads(tick.read_text())) if tick.is_file() else {}
    df = lp.ipo_history(_rows(sec), tickers, lp.store_first_bar(daily), _covers(sec))
    df = lp.fill_first_sessions(df, _first_bars(data))
    data.mkdir(parents=True, exist_ok=True)  # a first run whose every request failed has no folder yet
    lp.write_history(df, data / "ipos.parquet")
    per_year = df["prospectus_filed"].map(lambda d: d.year).value_counts().sort_index()
    meta = {"built_at": datetime.now(UTC).isoformat(timespec="seconds"), "rows": int(len(df)),
            "by_kind": {str(k): int(v) for k, v in df["kind"].value_counts().sort_index().items()},
            "per_year": {int(k): int(v) for k, v in per_year.items()},
            "with_symbol": int((df["symbol"] != "").sum()),
            "with_first_session": int(df["first_session"].notna().sum()),
            "first_session_from_alpaca": int((df["first_session_source"] == "alpaca_bar").sum()),
            "with_offer_price": int(df["offer_price"].notna().sum()),
            "quarters": len(list((sec / "full-index").glob("*.json"))), **(extra or {})}
    _atomic(data / "build.json", json.dumps(meta, indent=1, default=str))
    return meta


def fetch(data: Path, daily: Path, ua: str, since: date, today: date | None = None, prices: bool = False) -> dict:
    t0 = time.monotonic()
    today = today or datetime.now(ls.ET).date()
    get = sec_response_getter(ua)
    sec = data / "sec"
    requests, errors = 0, {}
    todo = quarters(since, today)
    print(f"{len(todo)} quarters since {since}; one request each to EDGAR (finished quarters are cached)…", flush=True)
    last, same = "", 0
    for y, q in todo:
        p = _extract(sec, y, q)
        if p.is_file() and (y, q) != todo[-1]:
            continue
        try:
            rows = ls.master_index_rows(get(FULL_INDEX_URL.format(y=y, q=q)))
        except Exception as e:  # noqa: BLE001  (one bad quarter must not stop the run; the next run reads it)
            errors[f"{y}Q{q}"] = f"{type(e).__name__}: {str(e)[:300]}"
            print(f"  {y} Q{q}: not read ({errors[f'{y}Q{q}']})", flush=True)
            sig = failure_signature(errors[f"{y}Q{q}"])
            same = same + 1 if sig == last else 1
            last = sig
            if same >= MAX_SAME_FAILURES:
                errors["stopped"] = (f"{same} quarters in a row failed the same way; the other quarters and the "
                                     "ticker table were not asked (the next run starts again from the first "
                                     "quarter not cached)")
                print(f"  {errors['stopped']}", flush=True)
                break
            continue
        finally:
            requests += 1
        last, same = "", 0
        _atomic(p, json.dumps([{**r, "filed": r["filed"].isoformat()} for r in rows]))
        print(f"  {y} Q{q}: {len(rows)} filings kept, {time.monotonic() - t0:.0f} s", flush=True)
    if "stopped" not in errors:
        try:
            body = get(TICKERS_URL).body
            lp.cik_tickers(json.loads(body))  # validate before it replaces the cached table
            _atomic(sec / "company_tickers.json", body.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            errors["company_tickers"] = f"{type(e).__name__}: {str(e)[:300]}"
        requests += 1
    if prices and "stopped" not in errors:
        requests += fetch_prices(data, ua, errors)
    return build(data, daily, {"requests_this_run": requests, "seconds": round(time.monotonic() - t0),
                               "errors": errors, "since": since.isoformat()})


def failure_signature(error: str) -> str:
    """An error with its quarter (the URL's YYYY/QTRn) and the answer's size taken out, so that the same failure of
    two quarters compares equal (a block page carries a reference id of its own, past the 80 characters quoted)."""
    return re.sub(r"\b\d+ bytes\b", "N bytes", re.sub(r"\b\d{4}/QTR[1-4]\b", "YYYY/QTRn", error))


def fetch_prices(data: Path, ua: str, errors: dict) -> int:
    """One request per listing of kind `ipo` whose cover was not read yet. Resumable: saved every 25."""
    sec = data / "sec"
    p = sec / "prospectus_covers.json"
    covers = json.loads(p.read_text()) if p.is_file() else {}
    tick = sec / "company_tickers.json"
    df = lp.ipo_history(_rows(sec), lp.cik_tickers(json.loads(tick.read_text())) if tick.is_file() else {})
    todo = [(r.cik, r.accession) for r in df.itertuples() if r.kind == "ipo" and r.accession not in covers]
    print(f"{len(todo)} prospectus covers to read (one request each)…", flush=True)
    headers = {"User-Agent": ua, "Accept-Encoding": "identity"}
    n = 0
    for i, (cik, acc) in enumerate(todo, 1):
        try:
            cover = ls.fetch_cover(f"edgar/data/{int(cik)}/{acc}.txt", headers)
            covers[acc] = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in (cover or {}).items()}
        except Exception as e:  # noqa: BLE001
            errors[acc] = f"{type(e).__name__}: {str(e)[:160]}"
        n += 1
        if i % 25 == 0 or i == len(todo):
            _atomic(p, json.dumps(covers))
            print(f"  {i} of {len(todo)} covers", flush=True)
    return n


def _first_bars(data: Path) -> dict[str, dict]:
    p = data / lp.ALPACA_FIRST_BARS
    return json.loads(p.read_text()) if p.is_file() else {}


def fetch_first_bars(data: Path, daily: Path, headers: dict[str, str], now: datetime | None = None, limit: int = 0,
                     **http_kw) -> dict:
    """One Alpaca request per history row of `lp.first_bar_todo`: the first daily bar (SIP, raw, at least 20
    minutes old) in `lp.first_bar_window` of its prospectus, judged by `lp.first_bar_verdict`. Saved every 25 and
    at the end; then the history is rebuilt (offline). A 400/404/422 is Alpaca's answer for a symbol it has no
    data for (final); any other failure is retried on a later run, and five in a row stop this one."""
    now = now or datetime.now(UTC)
    sec = data / "sec"
    tick = sec / "company_tickers.json"
    if not tick.is_file():
        raise SystemExit("No history yet: run --since 2016-01-01 first")
    df = lp.ipo_history(_rows(sec), lp.cik_tickers(json.loads(tick.read_text())), lp.store_first_bar(daily))
    found = _first_bars(data)
    todo = lp.first_bar_todo(df, found)
    if limit:
        todo = todo[:limit]
    http_kw.setdefault("budget", Budget(ls.BUDGET_BARS, BudgetSpec(windows=((FIRST_BARS_PER_MIN, 60.0),))))
    print(f"{len(todo)} listings without a first session; one Alpaca request each, at most "
          f"{FIRST_BARS_PER_MIN} a minute…", flush=True)
    t0, asked, errors, in_a_row = time.monotonic(), 0, {}, 0
    latest = now - ls.SIP_DELAY
    for i, r in enumerate(todo, 1):
        if i > 1 and (i - 1) % 25 == 0:
            _atomic(data / lp.ALPACA_FIRST_BARS, json.dumps(found, indent=0, sort_keys=True))
            print(f"  {i - 1} of {len(todo)}, {time.monotonic() - t0:.0f} s", flush=True)
        start, end = lp.first_bar_window(r["prospectus_filed"])
        end_ts = min(datetime(end.year, end.month, end.day, tzinfo=UTC), latest)
        params = {"timeframe": "1Day", "start": datetime(start.year, start.month, start.day, tzinfo=UTC).isoformat(),
                  "end": end_ts.isoformat(), "limit": 10, "adjustment": "raw", "feed": "sip"}
        asked += 1
        try:
            doc = http.get_json(ls.BARS_URL.format(symbol=r["symbol"]), source=ls.BUDGET_BARS, headers=headers,
                                params=params, timeout=60, max_bytes=4 * 1024 * 1024, **http_kw)
        except http.HttpError as e:
            if e.status in (400, 404, 422):
                found[r["accession"]] = {"symbol": r["symbol"], "verdict": "no_data", "first_bar": None,
                                         "asked_at": now.isoformat(timespec="seconds")}
                in_a_row = 0
                continue
            errors[r["accession"]] = f"{r['symbol']}: {str(e)[:160]}"
            in_a_row += 1
            if in_a_row >= MAX_ERRORS_IN_A_ROW:
                print(f"  {in_a_row} failures in a row, stopping; the next run goes on from here", flush=True)
                break
            continue
        in_a_row = 0
        bars = doc.get("bars") if isinstance(doc, dict) else None
        t = ls._parse_utc(bars[0].get("t")) if isinstance(bars, list) and bars and isinstance(bars[0], dict) else None
        first = ls._et_date(t) if t is not None else None
        if first is not None and nyse.is_session(first) and nyse.close_utc(first) + ls.BAR_DELAY > now:
            continue  # its bar is not final yet: a later run asks again
        verdict = lp.first_bar_verdict(first, r["prospectus_filed"], window_over=end_ts.date() >= end)
        if verdict is not None:
            found[r["accession"]] = {"symbol": r["symbol"], "verdict": verdict,
                                     "first_bar": first.isoformat() if first else None,
                                     "asked_at": now.isoformat(timespec="seconds")}
    _atomic(data / lp.ALPACA_FIRST_BARS, json.dumps(found, indent=0, sort_keys=True))
    by = {}
    for v in found.values():
        by[v["verdict"]] = by.get(v["verdict"], 0) + 1
    return build(data, daily, {"alpaca_requests_this_run": asked, "alpaca_errors": errors,
                               "alpaca_verdicts": dict(sorted(by.items()))})


def status(data: Path) -> None:
    sec = data / "sec"
    print(f"cached quarters: {len(list((sec / 'full-index').glob('*.json')))}")
    p = data / "build.json"
    print("build.json: " + (p.read_text() if p.is_file() else "not yet"))


def eligible(first_session: date) -> None:
    print(f"First session {first_session}. The earliest date each rule could consider the stock:")
    for r in lp.first_eligible(first_session):
        print(f"  {r['first_eligible']}  {r['rule']} ({r['state']}): {r['needs']}")


def show(root: Path, symbol: str, now: datetime) -> int:
    L = lp.Listings.load(root)
    hit = L.listing(symbol, now)
    if hit is None:
        print(f"{symbol}: not in the watch's store under {root / 'alt' / 'listings'}")
        return 1
    for k, v in hit.items():
        print(f"{k:22} {v}")
    print("\nWhat it rests on (available_ts: the first time Kaibot could have known it):")
    cols = ["available_ts", "ingested_ts", "source", "kind", "form", "filed", "first_trade_date"]
    print(L.evidence(symbol, now)[cols].to_string(index=False))
    if hit["first_session"] is not None:
        print()
        eligible(hit["first_session"])
    return 0


def report(root: Path, now: datetime) -> int:
    rep = lp.report(lp.Listings.load(root), now, nights=30)
    print(json.dumps(rep, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=switches.text("KAIBOT_ENV_FILE") or DEFAULT_ENV,
                    help="env file with SEC_USER_AGENT (kept outside the repo)")
    ap.add_argument("--since", type=date.fromisoformat, default=date(2016, 1, 1))
    ap.add_argument("--data", default=str(ROOT / "data" / "listings"))
    ap.add_argument("--daily", default=str(ROOT / "data" / "daily"), help="the daily bar store, for first sessions")
    ap.add_argument("--prices", action="store_true", help="also read each prospectus cover: one request a listing")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--first-bars", action="store_true",
                   help="first sessions of listings outside data/daily from Alpaca daily bars (paper keys)")
    ap.add_argument("--limit", type=int, default=0, help="--first-bars: at most this many requests (0: all)")
    g.add_argument("--build", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--eligible", type=date.fromisoformat, metavar="FIRST_SESSION")
    g.add_argument("--show", metavar="SYMBOL")
    g.add_argument("--report", action="store_true")
    ap.add_argument("--root", default=switches.text("KAIBOT_DATA_DIR") or str(ROOT / "data"),
                    help="--show, --report: the data folder the nightly watch writes under")
    a = ap.parse_args(argv)
    data = Path(a.data).expanduser()
    if a.eligible:
        eligible(a.eligible)
        return 0
    if a.show or a.report:
        now, root = datetime.now(UTC), Path(a.root).expanduser()
        return show(root, a.show.upper(), now) if a.show else report(root, now)
    if a.status:
        status(data)
        return 0
    if a.build:
        meta = build(data, Path(a.daily).expanduser())
    elif a.first_bars:
        load_env_file(Path(a.env).expanduser())
        key = os.environ.get("ALPACA_API_KEY", "")
        secret = os.environ.get("ALPACA_SECRET") or os.environ.get("ALPACA_API_SECRET", "")
        if not key or not secret:
            print("Missing the Alpaca paper keys in the env file (ALPACA_API_KEY, ALPACA_SECRET)")
            return 1
        headers = ls._alpaca_headers(ls.Creds(key, secret, ""))
        meta = fetch_first_bars(data, Path(a.daily).expanduser(), headers, limit=a.limit)
        print(f"Alpaca: {meta['alpaca_requests_this_run']} requests, verdicts {meta['alpaca_verdicts']}")
        meta["errors"] = meta.get("alpaca_errors")
    else:
        load_env_file(Path(a.env).expanduser())
        ua = os.environ.get("SEC_USER_AGENT", "")
        if "@" not in ua:
            print("Missing SEC_USER_AGENT in the env file ('Name email@domain')")
            return 1
        meta = fetch(data, Path(a.daily).expanduser(), ua, a.since, prices=a.prices)
    print(f"\n{meta['rows']} listings {meta['by_kind']} -> {data / 'ipos.parquet'}")
    print(f"with today's ticker: {meta['with_symbol']}; with a first session: {meta['with_first_session']} "
          f"({meta['first_session_from_alpaca']} from Alpaca); with an offer price: {meta['with_offer_price']}")
    if meta.get("errors"):
        print(f"not read (the next run tries again): {meta['errors']}")
    return 1 if meta.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
