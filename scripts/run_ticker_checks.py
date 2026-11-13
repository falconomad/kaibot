#!/usr/bin/env python3
"""M5 S68 (#96): answer the dashboard's ticker checks (kaibot.ticker_check rows left `pending` by kb_check_ticker).

For each pending ticker: the broker's asset record (listed, tradable), completed daily SIP bars (history, price,
20-day $ volume) and Finnhub's industry (sector ETF; SPY when unknown). Read-only calls on free tiers, inside the
budgets of config/sources.yaml. Run it next to the engine (it is a separate process and never trades):

    python scripts/run_ticker_checks.py [--env ~/Projects/kaibot-secrets/.env] [--once]

Without --once it polls every 5 seconds, so a check on the Strategy page answers within a few seconds."""

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=os.path.expanduser("~/Projects/kaibot-secrets/.env"))
    ap.add_argument("--once", action="store_true", help="answer what is pending, then exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.environ.setdefault("KAIBOT_ENV_FILE", a.env)
    from kaibot.engine.alpaca import AlpacaData, AlpacaPaper
    from kaibot.engine.config import Settings
    from kaibot.engine.store import Store

    st = Settings.from_env()  # paper endpoint only; a live URL is refused
    broker, data = AlpacaPaper(st.alpaca_key, st.alpaca_secret), AlpacaData(st.alpaca_key, st.alpaca_secret)
    store = Store(st.supabase_url, st.supabase_service_key)
    kw = {"asset_fn": broker.asset,
          "bars_fn": lambda s, start, end: data.bars([s], start, end, feed="sip", timeframe="1Day").get(s),
          "industry_fn": finnhub_industry(os.environ.get("FINNHUB_API_KEY"))}
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
