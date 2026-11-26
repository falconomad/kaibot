"""Alpha Vantage EARNINGS backfill, free tier only, research only (decision 20; docs/ops/alphavantage.md).

  python scripts/fetch_av_earnings.py                    the calendar (1 request), then up to 19 more today (13 s apart)
  python scripts/fetch_av_earnings.py --no-calendar      the backfill only (still keeps 1 request for the calendar)
  python scripts/fetch_av_earnings.py --budget 5         spend at most 5 of today's requests (never above 23)
  python scripts/fetch_av_earnings.py --status           offline: progress and today's count; sends nothing
  python scripts/fetch_av_earnings.py --check-key        is a key configured? (says yes/no, never shows it)

Symbols: the engine's universe (the 500 current S&P 500 members, kaibot.ingest.sp500_pit), alphabetical. Uncached
symbols first; cached ones older than 100 days are re-read only once every symbol is cached. Stops at once on
Alpha Vantage's rate-limit answer ("Information" / "Note"), which is never stored as data.

First, once a UTC day: the upcoming-earnings calendar (EARNINGS_CALENDAR, horizon 3month: ONE request for every US
symbol), filtered to the engine universe and written to kaibot.earnings_calendar through
kaibot.earnings_calendar_refresh (migration 0054) with the Supabase service key of the --env file (never printed).
DISPLAY ONLY: the dashboard's "Upcoming earnings" card reads it; the engine never does. No service key, or the
migration not applied yet: a WARNING, the CSV stays cached and is pushed again (no new request) by the next run.

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


def make_store(env_file: str | None):
    """The service-role client for the calendar write, or None (a WARNING says why). The key is never printed."""
    from kaibot.common.secret_files import read_names
    from kaibot.engine.config import DEFAULT_SUPABASE_URL
    from kaibot.engine.store import Store

    names = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"]
    env = read_names(env_file, names) if env_file else {}
    key = env.get("SUPABASE_SERVICE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        return None
    return Store(env.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/"), key).quick(timeout=20.0)


def run_calendar(symbols: list[str], data: Path, get, budget: int, env_file: str | None, store_factory=None) -> int:
    from datetime import UTC, datetime

    res, st = av.fetch_calendar(symbols, data, get, budget=budget)
    print(f"upcoming-earnings calendar: {res.status}" + (f", {len(res.rows)} rows of the universe" if res.rows else ""))
    if res.status not in ("fetched", "cached"):
        return res.exit_code
    if res.status == "cached" and st.calendar_pushed_day == st.day:
        return 0
    store = (store_factory or make_store)(env_file)
    if store is None:
        print("WARNING: no SUPABASE_SERVICE_KEY in the --env file: the calendar is cached on disk only "
              "(the dashboard card stays empty)")
        return 0
    try:
        out = av.push_calendar(res.rows, datetime.now(UTC), store)
    except Exception as e:  # noqa: BLE001 - display data: never blocks the backfill
        msg = str(e)
        hint = (" (migration 0054 not applied yet: docs/ops/migrations_pending.md)"
                if "PGRST202" in msg or "earnings_calendar_refresh" in msg or "404" in msg else "")
        print(f"WARNING: calendar not written to the database{hint}: {msg[:200]}")
        return 0
    av.mark_pushed(data, st.day)
    print(f"calendar written: {out}")
    return 0


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
    ap.add_argument("--no-calendar", action="store_true",
                    help="skip the upcoming-earnings calendar (its 1 request stays reserved for today)")
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
    get = av.http_getter(key)
    cal_rc = 0 if a.no_calendar else run_calendar(symbols, data, get, a.budget, a.env)
    res, st = av.backfill(symbols, data, get, budget=a.budget, reserve_calendar=True)
    print(summary(symbols, st, a.budget, res))
    print(json.dumps(av.progress(symbols, st, a.budget)))
    return res.exit_code or cal_rc


if __name__ == "__main__":
    sys.exit(main())
