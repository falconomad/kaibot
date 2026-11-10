#!/usr/bin/env python3
"""Smoke-test Kaibot's free API keys. Standard library only; prints status codes, NEVER key values.

Usage (on your Mac, normal terminal):
    python3 scripts/smoke_test_keys.py ~/Projects/kaibot-secrets/.env

Each line: PASS / FAIL / SKIP, the endpoint family, and a short non-secret detail.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = "kaibot-smoke-test/0.1"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        env[k.strip()] = v
    return env


def get(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> tuple[int, object]:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body[:200]
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:  # network errors
        return 0, str(e)[:80]


def report(ok: bool, name: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<34} {detail}")


def main() -> int:
    env = load_env(Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(".env"))
    fails = 0

    ak = env.get("ALPACA_API_KEY")
    asec = env.get("ALPACA_API_SECRET") or env.get("ALPACA_SECRET")
    if ak and asec:
        h = {"APCA-API-KEY-ID": ak, "APCA-API-SECRET-KEY": asec}
        code, acct = get("https://paper-api.alpaca.markets/v2/account", h)
        ok = code == 200 and isinstance(acct, dict)
        report(ok, "Alpaca paper account",
               f"status={acct.get('status')} equity={acct.get('equity')} created={str(acct.get('created_at'))[:10]}"
               if ok else f"http {code} (are these PAPER keys?)")
        fails += not ok
        if ok:
            _, orders = get("https://paper-api.alpaca.markets/v2/orders?status=all&limit=50", h)
            _, pos = get("https://paper-api.alpaca.markets/v2/positions", h)
            n_o = len(orders) if isinstance(orders, list) else "?"
            n_p = len(pos) if isinstance(pos, list) else "?"
            fresh = n_o == 0 and n_p == 0
            print(f"{'PASS' if fresh else 'WARN'}  {'Alpaca account is unused':<34} orders={n_o} positions={n_p}"
                  + ("" if fresh else "  <- shared with bloodbath? use a fresh paper account"))
        for name, url in [
            ("Alpaca IEX snapshot (SPY)", "https://data.alpaca.markets/v2/stocks/SPY/snapshot?feed=iex"),
            ("Alpaca SIP bars (>=15 min old)",
             "https://data.alpaca.markets/v2/stocks/SPY/bars?timeframe=1Day&limit=5&feed=sip"),
            ("Alpaca news (Benzinga)", "https://data.alpaca.markets/v1beta1/news?symbols=AAPL&limit=3"),
            ("Alpaca movers (free plan?)", "https://data.alpaca.markets/v1beta1/screener/stocks/movers?top=5"),
            ("Alpaca corporate actions", "https://data.alpaca.markets/v1/corporate-actions?symbols=AAPL&types=forward_split"),
            ("Alpaca clock", "https://paper-api.alpaca.markets/v2/clock"),
        ]:
            code, _ = get(url, h)
            report(code == 200, name, f"http {code}")
            fails += code != 200 and "movers" not in name and "corporate" not in name
    else:
        print("SKIP  Alpaca                             ALPACA_API_KEY / ALPACA_SECRET missing")

    fk = env.get("FINNHUB_API_KEY")
    if fk:
        for name, path in [
            ("Finnhub quote", "quote?symbol=AAPL"),
            ("Finnhub company news", "company-news?symbol=AAPL&from=2026-11-01&to=2026-11-10"),
            ("Finnhub earnings calendar", "calendar/earnings?from=2026-11-10&to=2026-11-17"),
            ("Finnhub basic financials", "stock/metric?symbol=AAPL&metric=all"),
            ("Finnhub recommendation trends", "stock/recommendation?symbol=AAPL"),
            ("Finnhub insider transactions", "stock/insider-transactions?symbol=AAPL"),
        ]:
            code, _ = get(f"https://finnhub.io/api/v1/{path}&token={fk}")
            report(code == 200, name, f"http {code}" + (" (premium on free plan)" if code == 403 else ""))
    else:
        print("SKIP  Finnhub                            FINNHUB_API_KEY missing")

    if env.get("FRED_API_KEY"):
        code, d = get("https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&limit=1"
                      f"&sort_order=desc&file_type=json&api_key={env['FRED_API_KEY']}")
        last = d["observations"][0]["date"] if code == 200 and isinstance(d, dict) and d.get("observations") else ""
        report(code == 200, "FRED (DGS10)", f"http {code} last={last}")
        fails += code != 200
    if env.get("BLS_API_KEY"):
        payload = json.dumps({"seriesid": ["CUUR0000SA0"], "startyear": "2026", "endyear": "2026",
                              "registrationkey": env["BLS_API_KEY"]}).encode()
        code, d = get("https://api.bls.gov/publicAPI/v2/timeseries/data/",
                      {"Content-Type": "application/json"}, payload)
        st = d.get("status") if isinstance(d, dict) else None
        report(st == "REQUEST_SUCCEEDED", "BLS (CPI)", f"http {code} {st}")
        fails += st != "REQUEST_SUCCEEDED"

    sua = env.get("SEC_USER_AGENT")
    code, _ = get("https://data.sec.gov/submissions/CIK0000320193.json",
                  {"User-Agent": sua or "kaibot smoke-test contact@example.com"})
    report(code == 200, "SEC EDGAR (no key)", f"http {code}" + ("" if sua else " (set SEC_USER_AGENT with your email)"))

    for name, url in [
        ("Polymarket Gamma (no key)", "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=1"),
        ("GDELT DOC (no key)", "https://api.gdeltproject.org/api/v2/doc/doc?query=tariff&mode=artlist&maxrecords=1&format=json"),
        ("Cboe VIX CSV (no key)", "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"),
        ("ApeWisdom (no key)", "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"),
    ]:
        code, _ = get(url)
        report(code == 200, name, f"http {code}")

    for k in ("ANTHROPIC_API_KEY", "MASSIVE_API_KEY", "SEC_USER_AGENT"):
        if not env.get(k):
            print(f"TODO  {k:<34} not set yet")
    print(f"\n{'All required checks passed.' if not fails else f'{fails} required check(s) failed.'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
