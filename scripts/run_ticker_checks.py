#!/usr/bin/env python3
"""M5 S68 (#96): answer the dashboard's ticker checks (kaibot.ticker_check rows left `pending` by kb_check_ticker).

For each pending ticker: the broker's asset record (listed, tradable), completed daily SIP bars (history, price,
20-day $ volume) and Finnhub's industry (sector ETF; SPY when unknown). Read-only calls on free tiers, inside the
budgets of config/sources.yaml. Run it next to the engine (it is a separate process and never trades):

    python scripts/run_ticker_checks.py [--env ~/Projects/kaibot-secrets/.env] [--data-env .../data.env] [--once]

Without --once it polls every 5 seconds, so a check on the Strategy page answers within a few seconds.

Keys (audit 2 7.2, #712; docs/ops/secrets.md). With data.env: Alpaca with ONLY the market-data pair of the second,
empty paper account (common/secret_files.py), for the asset record and the bars; from the engine's .env it keeps
only SUPABASE_URL, the Supabase service key (it answers kaibot.ticker_check rows; no helper function for them yet)
and FINNHUB_API_KEY: the trading pair's values are never sliced out of their lines or kept, so this process never
holds the trading key. Without data.env (Product, 26 Nov 2026: nothing stops before the owner's setup): as before
#712, the engine's .env and its paper trading key, with a WARNING."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.common import http  # noqa: E402
from kaibot.engine import tickercheck  # noqa: E402

log = logging.getLogger("ticker_checks")
FINNHUB_PROFILE = "https://finnhub.io/api/v1/stock/profile2"


def finnhub_industry(key: str | None):
    def industry(symbol: str):
        if not key:
            return None
        p = http.get_json(FINNHUB_PROFILE, source="finnhub", params={"symbol": symbol, "token": key}, timeout=20)
        return (p.get("name"), p.get("finnhubIndustry")) if isinstance(p, dict) and p else None

    return industry


ENV_NAMES = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "FINNHUB_API_KEY")


def load_keys(env_file: str, data_file: str | None) -> dict[str, str]:
    """The worker's settings: the data pair from data.env and ENV_NAMES from the engine's .env, nothing else (#712).
    SystemExit with what to do when one is missing."""
    from kaibot.common.secret_files import data_keys, read_names
    from kaibot.engine.config import DEFAULT_SUPABASE_URL

    key, secret = data_keys(data_file)
    env = read_names(env_file, ENV_NAMES)
    service = env.get("SUPABASE_SERVICE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not service:
        raise SystemExit(f"missing SUPABASE_SERVICE_KEY in {env_file}")
    return {"alpaca_key": key, "alpaca_secret": secret, "supabase_url": env.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL,
            "service_key": service, "finnhub": env.get("FINNHUB_API_KEY", "")}


def legacy_keys(env_file: str) -> dict[str, str]:
    """Before data.env exists (Product, 26 Nov 2026): the worker's settings as before #712, from the engine's .env."""
    os.environ.setdefault("KAIBOT_ENV_FILE", env_file)
    from kaibot.engine.config import Settings

    st = Settings.from_env()  # paper endpoint only; a live URL is refused
    return {"alpaca_key": st.alpaca_key, "alpaca_secret": st.alpaca_secret, "supabase_url": st.supabase_url,
            "service_key": st.supabase_service_key, "finnhub": os.environ.get("FINNHUB_API_KEY", "")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=os.path.expanduser("~/Projects/kaibot-secrets/.env"))
    ap.add_argument("--data-env", default=None, help="the market-data key file (docs/ops/secrets.md)")
    ap.add_argument("--once", action="store_true", help="answer what is pending, then exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from kaibot.common.secret_files import data_env_path, data_env_present
    from kaibot.engine.alpaca import AlpacaData, AlpacaPaper
    from kaibot.engine.store import Store

    if data_env_present(a.data_env):
        k = load_keys(a.env, a.data_env)  # never the trading pair (#712)
        log.info("ticker checks: Alpaca with the market-data key of the second paper account (no trading key here)")
    else:
        k = legacy_keys(a.env)
        log.warning("WARNING: no market-data key file (%s): the ticker checks still use the paper TRADING key, as "
                    "before #712. Set up data.env (docs/ops/secrets.md, 'The data key')", data_env_path(a.data_env))
    broker, data = AlpacaPaper(k["alpaca_key"], k["alpaca_secret"]), AlpacaData(k["alpaca_key"], k["alpaca_secret"])
    store = Store(k["supabase_url"], k["service_key"])
    kw = {"asset_fn": broker.asset,
          "bars_fn": lambda s, start, end: data.bars([s], start, end, feed="sip", timeframe="1Day").get(s),
          "industry_fn": finnhub_industry(k["finnhub"] or None)}
    while True:
        try:
            for r in tickercheck.process(store, **kw):
                print(json.dumps(r))
        except Exception:
            log.exception("ticker check pass failed (will retry)")
        if a.once:
            break
        time.sleep(5)


if __name__ == "__main__":
    main()
