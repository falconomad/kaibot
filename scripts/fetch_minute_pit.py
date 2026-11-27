"""The 15:50 minutes of the whole point-in-time universe (issue #794; decision 19 option B; ingest/minute_pit.py).

    .venv/bin/python scripts/fetch_minute_pit.py --status                 # offline: the plan, no request
    .venv/bin/python scripts/fetch_minute_pit.py --env ~/Projects/kaibot-secrets/.env --max-minutes 220
    .venv/bin/python scripts/fetch_minute_pit.py --report                 # offline: coverage per year

Research data only: writes data/bars_1550 (ignored), which scripts/near_close_1550_rerun.py reads through
replay/minute_1550.py; never data/bars_1m, data/daily or a setting, and the live engine reads none of it.

Data rules: Alpaca SIP 1-minute bars (free when at least 15 minutes old; everything asked is from 2016 to the end of
the research window, config/holdout.yaml, so nothing from the hold-out), `adjustment=split` as data/bars_1m.
Requests run under the shared `alpaca` budget (config/sources.yaml) and at most --per-min a minute for this process
(default 40, the slow rate of scripts/fetch_daily.sh beside the engine; never above 120). About 2,300 requests for
the plain keys and current members (one a session) and a few thousand for the former members (one key-month at a
time). Resumable: a finished month is never asked again; --max-minutes stops it between units so a runner job ends
cleanly (the research runner also pauses it in the quiet window around the close). Each run ends with the coverage
report. Never prints key values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.common import switches  # noqa: E402
from kaibot.engine.config import load_env_file  # noqa: E402
from kaibot.evaluation import holdout  # noqa: E402
from kaibot.ingest import minute_pit as mp  # noqa: E402
from kaibot.ingest.alpaca_history import Alpaca  # noqa: E402
from kaibot.ingest.certs import ensure_ca_bundle  # noqa: E402
from kaibot.ingest.daily_store import DailyStore  # noqa: E402
from kaibot.ingest.sp500_pit import Membership  # noqa: E402

DEFAULT_ENV = "~/Projects/kaibot-secrets/.env"
PER_MIN_DEFAULT, PER_MIN_MAX = 40, 120


def build_targets(data: Path, universe_file: Path, start: date, end: date) -> list[mp.Target]:
    store = DailyStore(data / "daily")
    keys = store.keys()
    if not keys:
        raise SystemExit(f"no daily store under {data / 'daily'}: run ./scripts/fetch_daily.sh first "
                         "(docs/ops/daily_bars.md)")
    cfg = yaml.safe_load(universe_file.read_text())
    return mp.targets(keys, Membership.load(universe=universe_file), cfg, start, end)


def report(data: Path, tgts, start: date, end: date) -> dict:
    cov = mp.coverage(data, tgts, start, end)
    text = mp.render(cov)
    out = data / mp.STORE
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage.json").write_text(json.dumps(cov, indent=1, default=str))
    (out / "coverage.md").write_text(text)
    print(text, flush=True)
    return cov


def main(argv: list[str] | None = None, api=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default=switches.text("KAIBOT_ENV_FILE") or DEFAULT_ENV, help=".env with the Alpaca keys")
    ap.add_argument("--data", default=str(ROOT / "data"), help="holds daily/ (the store), bars_1m/, bars_1550/")
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--start", type=date.fromisoformat, default=mp.START)
    ap.add_argument("--end", type=date.fromisoformat, default=None,
                    help="last session (default and latest: the research window's end in config/holdout.yaml)")
    ap.add_argument("--per-min", type=int, default=PER_MIN_DEFAULT, help=f"requests a minute (at most {PER_MIN_MAX})")
    ap.add_argument("--max-minutes", type=float, default=0.0, help="stop between units after this long (0: never)")
    ap.add_argument("--limit", type=int, default=0, help="at most this many units (0: all)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="offline: what is left to fetch, no request")
    g.add_argument("--report", action="store_true", help="offline: the coverage report only")
    a = ap.parse_args(argv)

    research_end = holdout.load().research_end
    end = a.end or research_end
    if end > research_end:
        raise SystemExit(f"--end {end} is inside the hold-out (research window ends {research_end}); minutes "
                         "after it are not fetched")
    if a.start < mp.START or a.start > end:
        raise SystemExit(f"--start must be between {mp.START} and --end")
    if not 0 < a.per_min <= PER_MIN_MAX:
        raise SystemExit(f"--per-min must be 1 to {PER_MIN_MAX} (Alpaca allows 200 a minute for the whole account)")
    if a.max_minutes < 0 or a.limit < 0:
        raise SystemExit("--max-minutes and --limit must not be negative")
    data = Path(a.data).expanduser()
    tgts = build_targets(data, Path(a.universe_file), a.start, end)
    print(f"Point-in-time universe {a.start} to {end}: {len(tgts)} keys "
          f"({sum(t.seen_by_runs for t in tgts)} as today's pit runs key them)", flush=True)
    if a.report:
        report(data, tgts, a.start, end)
        return 0
    units = mp.plan(data, tgts)
    if a.status:
        by = {}
        for u in units:
            by[u.kind] = by.get(u.kind, 0) + 1
        print(f"{len(units)} units left {dict(sorted(by.items()))}, about {sum(u.est_requests for u in units)} "
              f"requests (at {a.per_min} a minute: {sum(u.est_requests for u in units) / a.per_min:.0f} minutes)")
        return 0
    if not units:
        print("Nothing left to fetch.")
        report(data, tgts, a.start, end)
        return 0
    if api is None:
        load_env_file(Path(a.env).expanduser())
        key = os.environ.get("ALPACA_API_KEY", "")
        secret = os.environ.get("ALPACA_SECRET") or os.environ.get("ALPACA_API_SECRET", "")
        if not key or not secret:
            print("Missing the Alpaca paper keys in the env file (ALPACA_API_KEY, ALPACA_SECRET)")
            return 2
        ensure_ca_bundle()
        api = Alpaca(key, secret, per_min=a.per_min)
    res = mp.fetch(api, data, tgts, max_minutes=a.max_minutes, limit=a.limit)
    print(f"Alpaca: {res['requests']} requests, {res['units_done']} units written, {res['units_left']} left.",
          flush=True)
    if res["errors"]:
        print(f"Not fetched (the next run tries again): {res['errors']}", flush=True)
    report(data, tgts, a.start, end)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
