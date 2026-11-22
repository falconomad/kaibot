#!/usr/bin/env python3
"""Measure what an index fund costs to trade, for cost model v2's index-fund bucket (M11 ticket 2.3, issue #454).
READ-ONLY: it sends GET requests only, places no order and changes no setting.

    ./scripts/calibrate_index_costs.sh                    the paper account's SPY fills against the quotes
    ./scripts/calibrate_index_costs.sh --quotes-only      no fills needed: quotes at the rule's check times
    ./scripts/calibrate_index_costs.sh --symbols SPY --days 90 --sessions 20

Why: cost model v2 charges SPY the bucket of every stock that trades over $500M a day, 1.75 bp a side. The SPY
rule's pre-registration says it needs fills under about half a basis point a side. This measures the fund's own
quoted spread at the moments it is traded, on free data.

What it reads (both hosts are refused unless they are the paper trading host and the market-data host):
1. GET /v2/account/activities?activity_types=FILL on the PAPER account, last --days days: the fills in --symbols
   during regular hours (budget `alpaca_trading`);
2. for each fill, GET /v2/stocks/{symbol}/quotes for the 5 seconds up to the fill, newest first, one row, feed
   `sip` (budget `alpaca`): the last national best bid and offer at or before the fill. A fill younger than 20
   minutes is left out (the free plan serves SIP data only when it is 15 minutes old);
3. with --quotes-only, or when there are fewer than 30 fills on 5 sessions: the same quote request at every half
   hour from 10:00 to 15:30 ET and at 15:50 ET on the last --sessions sessions that are fully over.

What it reports, per symbol:
- the QUOTED half-spread in bp, (ask - bid) / 2 / mid: mean, median, 90th percentile, by time of day;
- for fills, what the fill paid against the mid in bp per side, and how many fills were at or inside the quote.
  The paper venue fills at the quote with no queue and no impact, so this is reported and NOT used.

The bucket value follows the rule fixed in config/costs_index_bucket.yaml before any measurement: the mean
quoted half-spread, rounded UP to the next 0.05 bp; from fills when there are at least 30 on at least 5 sessions,
else from the quote sample. The script prints the block to put into that file. It does not write the file: a cost
parameter changes by pull request, with the table (docs/research/results/index_cost_calibration.md).

Paced at --per-min 40 requests a minute (the engine counts its own requests in its own process): the quote
sample of 20 sessions is 260 requests, about seven minutes.

Writes <data>/reports/index_cost_calibration.json and .md. Never prints a key, an order id or an account number.
Exit 0 when a value was measured, 3 when there was too little to measure, 2 when a request failed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time as _time
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.engine.config import DATA_URL, PAPER_TRADING_URL, load_env_file  # noqa: E402

DEFAULT_ENV = Path.home() / "Projects" / "kaibot-secrets" / ".env"
ET = ZoneInfo("America/New_York")
PAGE, MAX_PAGES = 100, 100
MIN_FILLS, MIN_SESSIONS = 30, 5  # config/costs_index_bucket.yaml, rule 2
SIP_DELAY = timedelta(minutes=20)
LOOKBACK = timedelta(seconds=5)
ROUND_UP_BP = 0.05
CHECKS = (*[time(h, m) for h in range(10, 16) for m in (0, 30)], time(15, 50))  # 10:00 .. 15:30, then 15:50
BUCKETS = (("first half hour", time(9, 30), time(10, 0)), ("midday", time(10, 0), time(15, 30)),
           ("last half hour", time(15, 30), time(16, 0)))


def keys(env: dict) -> tuple[str, str]:
    key = env.get("ALPACA_API_KEY") or ""
    secret = env.get("ALPACA_SECRET") or env.get("ALPACA_API_SECRET") or ""
    if not key or not secret:
        raise SystemExit("missing ALPACA_API_KEY / ALPACA_SECRET in the .env (values are never printed)")
    return key, secret


def paper_base(env: dict) -> str:
    base = (env.get("ALPACA_BASE_URL") or PAPER_TRADING_URL).rstrip("/")
    if base != PAPER_TRADING_URL:
        raise SystemExit(f"refused: ALPACA_BASE_URL is not the paper endpoint ({PAPER_TRADING_URL}); "
                         "Kaibot is paper-only")
    return base


def _when(text: str) -> datetime:
    """An RFC-3339 time with up to nanoseconds, as UTC."""
    s = str(text).replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        zone = rest[len(frac):] or "+00:00"
        s = f"{head}.{frac[:6].ljust(6, '0')}{zone}"
    t = datetime.fromisoformat(s)
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC)


def _stamp(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def regular_hours(t: datetime) -> bool:
    et = t.astimezone(ET)
    return et.weekday() < 5 and time(9, 30) <= et.time() < time(16, 0)


def fills(http, base: str, symbols: set[str], now: datetime, days: int) -> tuple[list[dict], bool]:
    """The paper account's fills in `symbols` during regular hours, oldest first, and whether the page limit
    cut the list short. Only side, quantity, price, time and symbol are kept."""
    params = {"activity_types": "FILL", "direction": "desc", "page_size": PAGE,
              "after": (now - timedelta(days=days)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    out: list[dict] = []
    for _ in range(MAX_PAGES):
        rows = http.call("GET", f"{base}/v2/account/activities", dict(params)) or []
        for r in rows:
            if not isinstance(r, dict) or r.get("symbol") not in symbols or r.get("side") not in ("buy", "sell"):
                continue
            try:
                t, px, qty = _when(r["transaction_time"]), float(r["price"]), float(r["qty"])
            except (KeyError, TypeError, ValueError):
                continue
            if px > 0 and qty > 0 and regular_hours(t) and now - t >= SIP_DELAY:
                out.append({"symbol": r["symbol"], "side": r["side"], "qty": qty, "price": px, "t": t})
        if len(rows) < PAGE:
            return sorted(out, key=lambda f: f["t"]), False
        params["page_token"] = rows[-1]["id"]
    return sorted(out, key=lambda f: f["t"]), True


def quote_at(http, symbol: str, t: datetime, feed: str = "sip") -> dict | None:
    """The last two-sided quote at or before `t` (within 5 seconds), or None."""
    got = http.call("GET", f"{DATA_URL}/v2/stocks/{symbol}/quotes",
                    {"start": _stamp(t - LOOKBACK), "end": _stamp(t), "limit": 10, "sort": "desc", "feed": feed})
    for q in (got or {}).get("quotes") or []:
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if bid > 0 and ask >= bid:  # a one-sided or crossed quote says nothing about the spread
            return {"bid": bid, "ask": ask, "t": _when(q["t"])}
    return None


def measure(quote: dict, fill: dict | None = None) -> dict:
    mid = (quote["bid"] + quote["ask"]) / 2.0
    out = {"mid": mid, "quoted_half_bp": (quote["ask"] - quote["bid"]) / 2.0 / mid * 1e4,
           "quote_age_s": None, "paid_bp": None, "inside": None}
    if fill is not None:
        sign = 1.0 if fill["side"] == "buy" else -1.0
        out.update(quote_age_s=(fill["t"] - quote["t"]).total_seconds(),
                   paid_bp=sign * (fill["price"] - mid) / mid * 1e4,
                   inside=bool(quote["bid"] - 1e-9 <= fill["price"] <= quote["ask"] + 1e-9))
    return out


def sample_times(now: datetime, sessions: int, holidays: set[date] = frozenset()) -> list[datetime]:
    """The check times of the last `sessions` weekdays that are fully over (a day is skipped when it is a known
    holiday; an unknown holiday simply returns no quote and is counted as such)."""
    out, d = [], now.astimezone(ET).date()
    while len({t.astimezone(ET).date() for t in out}) < sessions and (now.astimezone(ET).date() - d).days < 60:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            out += [datetime.combine(d, c, ET).astimezone(UTC) for c in CHECKS]
    return sorted(out)


def _bucket(t: datetime) -> str:
    et = t.astimezone(ET).time()
    return next((name for name, a, b in BUCKETS if a <= et < b), "other")


def _stats(values: list[float]) -> dict | None:
    if not values:
        return None
    v = sorted(values)
    return {"n": len(v), "mean": statistics.fmean(v), "median": statistics.median(v),
            "p90": v[min(len(v) - 1, math.ceil(0.9 * len(v)) - 1)], "max": v[-1]}


def round_up(bp: float, step: float = ROUND_UP_BP) -> float:
    return round(math.ceil(bp / step - 1e-9) * step, 2)


def summarise(symbol: str, rows: list[dict]) -> dict:
    """One symbol's table from its measured rows ({"t", "kind": "fill"|"quote", "quoted_half_bp", "paid_bp", ...})."""
    out = {"symbol": symbol}
    for kind in ("fill", "quote"):
        mine = [r for r in rows if r["kind"] == kind]
        days = {r["t"].astimezone(ET).date() for r in mine}
        out[kind] = {"n": len(mine), "sessions": len(days),
                     "first": min(days).isoformat() if days else None, "last": max(days).isoformat() if days else None,
                     "quoted_half_bp": _stats([r["quoted_half_bp"] for r in mine]),
                     "mean_mid": statistics.fmean([r["mid"] for r in mine]) if mine else None,
                     "by_time": {name: _stats([r["quoted_half_bp"] for r in mine if _bucket(r["t"]) == name])
                                 for name, _, _ in BUCKETS}}
    f = [r for r in rows if r["kind"] == "fill"]
    out["fill"].update(paid_bp=_stats([r["paid_bp"] for r in f]),
                       inside_quote_share=sum(r["inside"] for r in f) / len(f) if f else None,
                       median_quote_age_s=statistics.median([r["quote_age_s"] for r in f]) if f else None)
    enough = out["fill"]["n"] >= MIN_FILLS and out["fill"]["sessions"] >= MIN_SESSIONS
    use = "fill" if enough else ("quote" if out["quote"]["n"] >= MIN_FILLS and out["quote"]["sessions"]
                                 >= MIN_SESSIONS else None)
    out["method"] = {"fill": "fills", "quote": "quotes", None: None}[use]
    out["half_spread_bp"] = round_up(out[use]["quoted_half_bp"]["mean"]) if use else None
    return out


def render(res: dict) -> str:
    out = ["# Index-fund cost calibration (cost model v2, ticket 2.3)", "",
           f"Measured {res['measured_at']} with `scripts/calibrate_index_costs.py` at code `{res['code_sha']}`. "
           f"Read-only: {res['requests']} GET requests, feed `{res['feed']}`. Fills: the paper account's, last "
           f"{res['days']} days. Quote sample: the rule's check times on the last {res['sessions']} sessions.", "",
           "The paper venue fills at the quote with no queue and no impact. What a fill paid beyond the quote is "
           "shown and is not used for the bucket.", "",
           "| Symbol | Source | Rows | Sessions | Window | Mean price | Quoted half-spread, bp: mean | median | "
           "90th pct | max |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s in res["symbols"]:
        for kind, name in (("fill", "at the account's fills"), ("quote", "at the check times")):
            k, q = s[kind], s[kind]["quoted_half_bp"]
            out.append(f"| {s['symbol']} | {name} | {k['n']} | {k['sessions']} | {k['first'] or '-'} to "
                       f"{k['last'] or '-'} | " + (f"{k['mean_mid']:.2f} | {q['mean']:.3f} | {q['median']:.3f} | "
                                                  f"{q['p90']:.3f} | {q['max']:.3f} |" if q else "- | - | - | - | - |"))
    out += ["", "| Symbol | Source | First half hour | Midday | Last half hour |", "|---|---|---|---|---|"]
    for s in res["symbols"]:
        for kind, name in (("fill", "fills"), ("quote", "check times")):
            cells = [f"{b['mean']:.3f} ({b['n']})" if (b := s[kind]["by_time"][n]) else "-" for n, _, _ in BUCKETS]
            out.append(f"| {s['symbol']} | {name} | " + " | ".join(cells) + " |")
    out += ["", "Mean quoted half-spread in bp (rows)."]
    for s in res["symbols"]:
        f = s["fill"]
        if f["paid_bp"]:
            out += ["", f"- {s['symbol']}: the fills paid {f['paid_bp']['mean']:.3f} bp a side against the mid on "
                        f"average (median {f['paid_bp']['median']:.3f}); {f['inside_quote_share']:.0%} were at or "
                        f"inside the quote; the quote was {f['median_quote_age_s']:.2f} s old at the median."]
    out += ["", "## The bucket value", ""]
    for s in res["symbols"]:
        if s["half_spread_bp"] is None:
            out.append(f"- {s['symbol']}: **not measured**: fewer than {MIN_FILLS} rows on {MIN_SESSIONS} sessions "
                       "from fills and from the quote sample.")
        else:
            out.append(f"- {s['symbol']}: **{s['half_spread_bp']:.2f} bp a side** (the mean quoted half-spread from "
                       f"{s['method']}, rounded up to the next {ROUND_UP_BP} bp). Cost model v2 charges 1.75 bp today.")
    v = res.get("bucket")
    if v:
        out += ["", "For `config/costs_index_bucket.yaml` (by pull request, with this table):", "", "```yaml",
                v.rstrip(), "```"]
    return "\n".join(out) + "\n"


def bucket_yaml(res: dict) -> str | None:
    got = [s for s in res["symbols"] if s["half_spread_bp"] is not None]
    if not got or len(got) != len(res["symbols"]):
        return None
    worst = max(got, key=lambda s: s["half_spread_bp"])  # one bucket for the listed funds: the dearest one's
    src = worst[{"fills": "fill", "quotes": "quote"}[worst["method"]]]
    return "\n".join([
        f"symbols: [{', '.join(s['symbol'] for s in got)}]",
        f"half_spread_bp: {worst['half_spread_bp']:.2f}",
        "source:",
        f"  method: {worst['method']}",
        f"  rows: {src['n']}",
        f"  sessions: {src['sessions']}",
        f"  window: [{src['first']}, {src['last']}]",
        f"  feed: {res['feed']}",
        f"  mean_quoted_half_bp: {src['quoted_half_bp']['mean']:.4f}",
        f"  median_quoted_half_bp: {src['quoted_half_bp']['median']:.4f}",
        f"  measured_at: {res['measured_at']}",
        f"  code_sha: {res['code_sha']}"]) + "\n"


class _Counting:
    """GET only, to the two allowed paths only, counted, and paced: the engine may be running on the same keys,
    and its request budget is counted in its own process, so this one keeps well under the account's limit."""

    def __init__(self, http, per_min: float = 0.0, sleep=None):
        self.http, self.n = http, 0
        self.gap = 60.0 / per_min if per_min > 0 else 0.0
        self.sleep = sleep or _time.sleep

    def call(self, method: str, url: str, params: dict | None = None, body: dict | None = None):
        if method != "GET" or body is not None:
            raise RuntimeError("this script is read-only: GET requests only")
        if not (url.startswith(PAPER_TRADING_URL + "/v2/account/activities")
                or url.startswith(DATA_URL + "/v2/stocks/")):
            raise RuntimeError("refused: not the paper account's activities or the market-data quotes")
        if self.gap and self.n:
            self.sleep(self.gap)
        self.n += 1
        return self.http.call(method, url, params)


def calibrate(trading, data_http, base: str, symbols: list[str], now: datetime, *, days: int, sessions: int,
              quotes_only: bool, feed: str = "sip", per_min: float = 0.0, sleep=None) -> dict:
    t, d = _Counting(trading, per_min, sleep), _Counting(data_http, per_min, sleep)
    rows: dict[str, list[dict]] = {s: [] for s in symbols}
    cut = False
    if not quotes_only:
        got, cut = fills(t, base, set(symbols), now, days)
        for f in got:
            q = quote_at(d, f["symbol"], f["t"], feed)
            if q is not None:
                rows[f["symbol"]].append({"t": f["t"], "kind": "fill", **measure(q, f)})
    for s in symbols:
        f = [r for r in rows[s] if r["kind"] == "fill"]
        if quotes_only or len(f) < MIN_FILLS or len({r["t"].astimezone(ET).date() for r in f}) < MIN_SESSIONS:
            for when in sample_times(now, sessions):
                q = quote_at(d, s, when, feed)
                if q is not None:
                    rows[s].append({"t": when, "kind": "quote", **measure(q)})
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    res = {"measured_at": now.astimezone(UTC).isoformat(timespec="seconds"), "code_sha": sha, "feed": feed,
           "days": days, "sessions": sessions, "quotes_only": quotes_only, "fills_list_cut_short": cut,
           "requests": t.n + d.n, "symbols": [summarise(s, rows[s]) for s in symbols]}
    res["bucket"] = bucket_yaml(res)
    return res


def main(argv: list[str] | None = None, trading=None, data_http=None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure an index fund's quoted spread at the moments it is traded "
                                             "(read-only).")
    ap.add_argument("--symbols", default="SPY", help="comma-separated index funds (default SPY)")
    ap.add_argument("--days", type=int, default=90, help="how far back to read the account's fills")
    ap.add_argument("--sessions", type=int, default=20, help="sessions in the quote sample")
    ap.add_argument("--quotes-only", action="store_true", help="skip the fills; sample quotes at the check times")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--per-min", type=float, default=40.0, help="requests a minute at most (the engine's own "
                    "requests are counted separately, so this stays low; 0 = no pause)")
    a = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if not symbols or a.days < 1 or a.sessions < MIN_SESSIONS:
        ap.error(f"needs a symbol, --days of at least 1 and --sessions of at least {MIN_SESSIONS}")
    path = Path(os.environ.get("KAIBOT_ENV_FILE") or DEFAULT_ENV)
    if trading is None or data_http is None:
        if not path.is_file():
            raise SystemExit(f"no .env at {path} (set KAIBOT_ENV_FILE=/path/to/.env)")
        load_env_file(path)
    env = dict(os.environ)
    base = paper_base(env)
    if trading is None or data_http is None:
        from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S, _Http

        key, secret = keys(env)
        trading = _Http(key, secret, source="alpaca_trading", timeout_s=ORDER_TIMEOUT_S, deadline_s=ORDER_DEADLINE_S)
        data_http = _Http(key, secret, source="alpaca", timeout_s=ORDER_TIMEOUT_S, deadline_s=ORDER_DEADLINE_S)
    try:
        res = calibrate(trading, data_http, base, symbols, now or datetime.now(UTC), days=a.days,
                        sessions=a.sessions, quotes_only=a.quotes_only, per_min=a.per_min)
    except Exception as e:  # an HTTP error: its status only (the adapter's message carries no key)
        print(f"FAILED: {type(e).__name__} {getattr(e, 'status', '')}".rstrip())
        return 2
    text = render(res)
    out = Path(a.data) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index_cost_calibration.json").write_text(json.dumps(res, indent=1, default=str))
    (out / "index_cost_calibration.md").write_text(text)
    print(text)
    return 0 if res["bucket"] else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
