#!/usr/bin/env python3
"""Turn shorting off on the Alpaca PAPER account (decision 15 section 11; docs/ops/paper_start.md step 3).

    ./scripts/alpaca_no_shorting.sh            (the wrapper: the engine's .env, the engine's Python)

Reads ALPACA_API_KEY and ALPACA_SECRET (or ALPACA_API_SECRET) from the engine's secrets file (KAIBOT_ENV_FILE,
default ~/Projects/kaibot-secrets/.env) with the engine's own loader (`kaibot.engine.config.load_env_file`), refuses
any base URL other than the paper endpoint, sends PATCH /v2/account/configurations {"no_shorting": true}, then reads
back GET /v2/account/configurations (`no_shorting`) and GET /v2/account (`shorting_enabled`). Never prints a key.
Exit 0 only when Alpaca confirms: no_shorting true and shorting_enabled false.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kaibot.engine.config import PAPER_TRADING_URL, load_env_file  # noqa: E402

DEFAULT_ENV = Path.home() / "Projects" / "kaibot-secrets" / ".env"


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


def run(http, base: str) -> int:
    """`http.call(method, url, params, body)`: the engine's Alpaca client (broker.alpaca._Http) or a fake."""
    http.call("PATCH", f"{base}/v2/account/configurations", None, {"no_shorting": True})
    cfg = http.call("GET", f"{base}/v2/account/configurations") or {}
    acct = http.call("GET", f"{base}/v2/account") or {}
    no_short, enabled = cfg.get("no_shorting"), acct.get("shorting_enabled")
    print(f"paper account configurations: no_shorting = {str(no_short).lower()}")
    print(f"paper account: shorting_enabled = {str(enabled).lower()}")
    ok = no_short is True and enabled is False
    print("OK: shorting is off on the paper account" if ok else "NOT CONFIRMED: check the Alpaca dashboard")
    return 0 if ok else 1


def main(argv: list[str] | None = None, http=None) -> int:
    path = Path(os.environ.get("KAIBOT_ENV_FILE") or DEFAULT_ENV)
    if not path.is_file():
        raise SystemExit(f"no .env at {path} (set KAIBOT_ENV_FILE=/path/to/.env)")
    load_env_file(path)
    env = dict(os.environ)
    base = paper_base(env)
    key, secret = keys(env)
    if http is None:
        from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S, _Http

        http = _Http(key, secret, source="alpaca_trading", timeout_s=ORDER_TIMEOUT_S, deadline_s=ORDER_DEADLINE_S)
    try:
        return run(http, base)
    except Exception as e:  # an HTTP error: its status only (the adapter's message carries no key)
        print(f"FAILED: {type(e).__name__} {getattr(e, 'status', '')}".rstrip())
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
