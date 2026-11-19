"""Nightly alt-data collection (run after 18:30 ET, outside market hours). Resumable and idempotent.

python scripts/collect_alt.py --env ~/Projects/kaibot-secrets/.env [--source finra|form4|gdelt|listings|all]
                              [--date YYYY-MM-DD]

Without --date it re-checks recent days (FINRA: last 5 trade dates; Form 4: filings of the last 3 days;
GDELT: yesterday and today, UTC; listings: the EDGAR daily indexes of the last 7 days, 120 on its first run), so a
missed night is caught up automatically. SEC_USER_AGENT is read from the env file, and for `listings` also the
Alpaca PAPER keys (one read-only request for the asset list, a few for first daily bars); no key is printed.
Writes data/alt/<source>/date=YYYY-MM-DD/part-*.parquet (see docs/ops/alt_data.md; listings: docs/ops/listings.md).
Exit code 1 if any source failed outright, or if one of the inputs of `listings` failed (the others are stored).
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
from kaibot.ingest.alt import finra, form4, gdelt, listings, store  # noqa: E402

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


def run_listings(data: Path, day: date | None, now: datetime) -> int:
    """New-listing watch (S102, #376): a shadow collector, the engine never reads it. With --date only the EDGAR
    daily index of that day is read again."""
    env = os.environ
    creds = listings.Creds(env.get("ALPACA_API_KEY", ""), env.get("ALPACA_SECRET") or env.get("ALPACA_API_SECRET", ""),
                           env.get("SEC_USER_AGENT", ""))
    with store.source_lock(data, listings.SOURCE):
        return listings.collect(data, creds, now=now, day=day, notice_urls=NOTICE_URLS)


NOTICE_URLS: list[str] = []  # --listing-notice: Nasdaq Trader notice pages to read once (older than the feed)
RUNNERS = {"finra": run_finra, "form4": run_form4, "gdelt": run_gdelt, "listings": run_listings}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True, help="env file with SEC_USER_AGENT (kept outside the repo)")
    ap.add_argument("--source", default="all", choices=[*RUNNERS, "all"])
    ap.add_argument("--date", type=date.fromisoformat, help="collect just this date (YYYY-MM-DD)")
    ap.add_argument("--data", default=os.environ.get("KAIBOT_DATA_DIR") or str(ROOT / "data"))
    ap.add_argument("--listing-notice", action="append", default=[], metavar="URL",
                    help="listings: also read this Nasdaq Trader notice page (https://www.nasdaqtrader.com/...)")
    a = ap.parse_args(argv)
    bad = [u for u in a.listing_notice if not u.startswith(listings.NOTICE_HOSTS)]
    if bad:
        ap.error(f"--listing-notice takes Nasdaq Trader pages only, not {bad[0][:60]!r}")
    NOTICE_URLS[:] = a.listing_notice
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
        except (SystemExit, listings.InputsFailed) as e:
            log.error("%s: %s", name, e)
            failed += 1
        except Exception:  # one source failing must not stop the others
            log.exception("%s: failed", name)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
