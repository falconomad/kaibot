#!/usr/bin/env python3
"""Smoke-test Kaibot's free API keys. Standard library only; prints status codes, NEVER key values.
Uses certifi's root certificates when certifi is installed and SSL_CERT_FILE is not set (macOS Python has none).

Usage (on your Mac, normal terminal):
    python3 scripts/smoke_test_keys.py ~/Projects/kaibot-secrets/.env

Each line: PASS / FAIL / SKIP, the endpoint family, and a short non-secret detail.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kaibot.ingest.certs import CERT_HINT, ensure_ca_bundle  # noqa: E402  (standard library only)

UA = "kaibot-smoke-test/0.1"
_no_response: list[str] = []  # hosts that gave no HTTP response at all (status 0); never holds keys


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
        _no_response.append(urllib.parse.urlsplit(url).hostname or "?")
        return 0, str(e)[:80]


def report(ok: bool, name: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<34} {detail}")


def check_alpaca_sip_history(h: dict[str, str], now: datetime | None = None) -> tuple[bool, str]:
    """Free-plan SIP history probe: 1-min SPY bars with feed=sip for a window that ENDS 20 min ago (>= 15 min old).
    PASS = the free plan serves SIP history (what scripts/fetch_history.py relies on)."""
    end = (now or datetime.now(UTC)) - timedelta(minutes=20)
    q = urllib.parse.urlencode({"timeframe": "1Min", "feed": "sip", "limit": 5, "adjustment": "raw",
                                "start": (end - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
    code, d = get(f"https://data.alpaca.markets/v2/stocks/SPY/bars?{q}", h)
    bars = d.get("bars") if isinstance(d, dict) else None
    if code == 200 and bars:
        return True, f"http 200 bars={len(bars)} first={str(bars[0].get('t'))[:16]} -> free plan serves SIP history"
    if code == 200:
        return False, "http 200 but no bars -> SIP history not confirmed"
    return False, f"http {code} -> free plan does NOT serve SIP history" + (" (subscription)" if code == 403 else "")


def check_databento(key: str) -> tuple[bool, str]:
    """Zero-cost auth check: metadata.list_datasets (HTTP Basic, key as user). Never downloads or spends credit."""
    auth = base64.b64encode(f"{key}:".encode()).decode()
    code, d = get("https://hist.databento.com/v0/metadata.list_datasets", {"Authorization": f"Basic {auth}"})
    if code == 200 and isinstance(d, list):
        return True, f"http 200 datasets={len(d)} EQUS.MINI={'yes' if 'EQUS.MINI' in d else 'no'} (no cost)"
    return False, f"http {code}" + (" (key rejected)" if code in (401, 403) else "")


def main(argv: list[str] | None = None) -> int:
    ensure_ca_bundle()  # before any network call
    _no_response.clear()
    argv = sys.argv if argv is None else argv
    env = load_env(Path(argv[1]).expanduser() if len(argv) > 1 else Path(".env"))
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
            ("Alpaca news (Benzinga)", "https://data.alpaca.markets/v1beta1/news?symbols=AAPL&limit=3"),
            ("Alpaca movers (free plan?)", "https://data.alpaca.markets/v1beta1/screener/stocks/movers?top=5"),
            ("Alpaca corporate actions", "https://data.alpaca.markets/v1/corporate-actions?symbols=AAPL&types=forward_split"),
            ("Alpaca clock", "https://paper-api.alpaca.markets/v2/clock"),
        ]:
            code, _ = get(url, h)
            report(code == 200, name, f"http {code}")
            fails += code != 200 and "movers" not in name and "corporate" not in name
        ok, detail = check_alpaca_sip_history(h)
        report(ok, "Alpaca SIP history (free plan?)", detail)
        fails += not ok
    else:
        print("SKIP  Alpaca                             ALPACA_API_KEY / ALPACA_SECRET missing")

    dk = env.get("DATABENTO_API_KEY")
    if dk:
        ok, detail = check_databento(dk)
        report(ok, "Databento auth (validation only)", detail)
        fails += not ok
    else:
        print("SKIP  Databento                          DATABENTO_API_KEY missing")

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
        code, d = get("https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS&limit=1"
                      f"&sort_order=desc&file_type=json&api_key={env['FRED_API_KEY']}")
        report(code == 200, "FRED VIX (VIXCLS, replaces Cboe CSV)", f"http {code}")
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
        ("GDELT DOC (no key, ~1 req/5 s)", "https://api.gdeltproject.org/api/v2/doc/doc?query=tariff&mode=artlist&maxrecords=1&format=json"),
        ("ApeWisdom (no key)", "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"),
    ]:
        code, _ = get(url)
        report(code == 200, name, f"http {code}")

    for k in ("ANTHROPIC_API_KEY", "MASSIVE_API_KEY", "SEC_USER_AGENT"):
        if not env.get(k):
            print(f"TODO  {k:<34} not set yet")
    print(f"\n{'All required checks passed.' if not fails else f'{fails} required check(s) failed.'}")
    if _no_response:
        print(CERT_HINT)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
