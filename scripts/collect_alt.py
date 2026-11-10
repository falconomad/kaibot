"""Nightly alt-data collection (run after 18:30 ET, outside market hours). Resumable and idempotent.

python scripts/collect_alt.py --env ~/Projects/kaibot-secrets/.env [--source finra|form4|gdelt|all] [--date YYYY-MM-DD]

Without --date it re-checks recent days (FINRA: last 5 trade dates; Form 4: filings of the last 3 days;
GDELT: yesterday and today, UTC), so a missed night is caught up automatically. Only SEC_USER_AGENT is read
from the env file; no key is printed. Writes data/alt/<source>/date=YYYY-MM-DD/part-*.parquet
(see docs/ops/alt_data.md).
Exit code 1 if any source failed outright.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.engine.config import load_env_file  # noqa: E402
from kaibot.ingest.alt import finra, form4, gdelt, store  # noqa: E402

log = logging.getLogger("collect_alt")


def universe_stocks(path: Path = ROOT / "config" / "universe_seed.yaml") -> list[str]:
    cfg = yaml.safe_load(path.read_text())
    return sorted({s for syms in cfg["sectors"].values() for s in syms})  # ETFs/SPY file no Form 4


def run_finra(data: Path, day: date | None, now: datetime) -> int:
    days = [day] if day else finra.default_days(now)
    with store.source_lock(data, finra.SOURCE):
        return sum(finra.collect(data, d, now=now) for d in days)


def run_form4(data: Path, day: date | None, now: datetime) -> int:
    ua = form4.check_user_agent(os.environ.get("SEC_USER_AGENT"))
    start, end = (day, day) if day else (now.date() - timedelta(days=3), now.date())
    with store.source_lock(data, form4.SOURCE):
        universe = form4.universe_ciks(universe_stocks(), form4.ticker_map(data, ua))
        return form4.collect(data, universe, ua, start, end, now=now)


def run_gdelt(data: Path, day: date | None, now: datetime) -> int:
    days = [day] if day else [now.date() - timedelta(days=1), now.date()]
    with store.source_lock(data, gdelt.SOURCE):
        return sum(gdelt.collect(data, d, now=now) for d in days)


RUNNERS = {"finra": run_finra, "form4": run_form4, "gdelt": run_gdelt}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, help="env file with SEC_USER_AGENT (kept outside the repo)")
    ap.add_argument("--source", default="all", choices=[*RUNNERS, "all"])
    ap.add_argument("--date", type=date.fromisoformat, help="collect just this date (YYYY-MM-DD)")
    ap.add_argument("--data", default=os.environ.get("KAIBOT_DATA_DIR") or str(ROOT / "data"))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not Path(a.env).expanduser().exists():
        ap.error(f"env file not found: {a.env}")
    load_env_file(Path(a.env).expanduser())
    now = datetime.now(UTC)
    data = Path(a.data).expanduser()
    failed = 0
    for name in RUNNERS if a.source == "all" else [a.source]:
        try:
            n = RUNNERS[name](data, a.date, now)
            log.info("%s: %d rows written", name, n)
        except SystemExit as e:
            log.error("%s: %s", name, e)
            failed += 1
        except Exception:  # one source failing must not stop the others
            log.exception("%s: failed", name)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
