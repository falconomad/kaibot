"""Alpha Vantage EARNINGS backfill, free tier only, research only (decision 20; docs/ops/alphavantage.md).

  python scripts/fetch_av_earnings.py                    up to 20 requests today (13 s apart), then a summary
  python scripts/fetch_av_earnings.py --budget 5         spend at most 5 of today's requests (never above 23)
  python scripts/fetch_av_earnings.py --status           offline: progress and today's count; sends nothing
  python scripts/fetch_av_earnings.py --check-key        is a key configured? (says yes/no, never shows it)

Symbols: the engine's universe (the 500 current S&P 500 members, kaibot.ingest.sp500_pit), alphabetical. Uncached
symbols first; cached ones older than 100 days are re-read only once every symbol is cached. Stops at once on
Alpha Vantage's rate-limit answer ("Information" / "Note"), which is never stored as data.

Key: ALPHAVANTAGE_API_KEY in ~/Projects/kaibot-secrets/data.env (KAIBOT_DATA_ENV_FILE names another file), else in
the --env file the research runner passes, else the environment. No key: a WARNING and exit 0 (nothing to do yet).
Writes data/earnings/alphavantage/ (raw/<SYMBOL>.json, state.json); prints no key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.ingest import alphavantage as av  # noqa: E402

DATA = ROOT / "data" / "earnings" / "alphavantage"


def universe() -> list[str]:
    """The engine universe today: the current S&P 500 members' tickers (500), alphabetical."""
    from kaibot.ingest.sp500_pit import Membership

    m = Membership.load()
    return sorted({m.spells[c][-1].tickers[-1] for c in m.current()})


def summary(symbols: list[str], state: av.State, budget: int, res: av.RunResult | None = None) -> str:
    p = av.progress(symbols, state, budget)
    lines = [f"Alpha Vantage EARNINGS cache: {p['cached']}/{p['total']} symbols cached, {p['no_data']} without data; "
             f"requests used today (UTC {p['day_utc'] or 'n/a'}): {p['requests_today']} of {budget}"
             + (f"; stopped today: {p['stopped_today']}" if p["stopped_today"] else "")]
    if res is not None:
        lines.append(f"this run: {res.used_this_run} requests, {len(res.cached)} cached, {len(res.no_data)} without "
                     f"data" + (f", stopped: {res.stopped}" if res.stopped else ""))
        if res.stopped == "rate_limit":
            lines.append("Alpha Vantage answered with its rate-limit text: nothing stored for it, no more requests "
                         "today (the key's 25 a day are shared with the owner's other uses)")
        if res.stopped == "key":
            lines.append("Alpha Vantage refused the key: check ALPHAVANTAGE_API_KEY in data.env "
                         "(docs/ops/alphavantage.md)")
    left = p["total"] - p["cached"] - p["no_data"]
    if left > 0 and budget:
        lines.append(f"{left} symbols left: about {-(-left // budget)} more days at {budget} a day")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", help="the engine's .env (the research runner passes it); data.env is read first")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--budget", type=int, default=av.DEFAULT_BUDGET,
                    help=f"requests today at most (default {av.DEFAULT_BUDGET}, never above {av.HARD_MAX_PER_DAY})")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true")
    g.add_argument("--check-key", action="store_true")
    a = ap.parse_args(argv)
    if not 0 <= a.budget <= av.HARD_MAX_PER_DAY:
        ap.error(f"--budget must be 0..{av.HARD_MAX_PER_DAY} (Alpha Vantage free tier: 25 a day, shared)")
    data = Path(a.data)
    key = "" if a.status else av.api_key(a.env)
    if a.check_key:
        print(f"{av.KEY_NAME}: " + ("configured (not shown)" if key else "NOT configured"))
        return 0 if key else 1
    symbols = universe()
    if a.status:
        st = av.State.load(data / "state.json")
        print(summary(symbols, st, a.budget))
        return 0
    if not key:
        print(f"WARNING: no {av.KEY_NAME} in data.env, the --env file or the environment: no Alpha Vantage request "
              "sent (research only, non-blocking). Setup: docs/ops/alphavantage.md")
        return 0
    res, st = av.backfill(symbols, data, av.http_getter(key), budget=a.budget)
    print(summary(symbols, st, a.budget, res))
    print(json.dumps(av.progress(symbols, st, a.budget)))
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
