#!/usr/bin/env python3
"""Show whether the Alpaca PAPER account posts FEE activities, and their shape (issue #238). Read-only.

    ./scripts/alpaca_fee_activities.sh         (the wrapper: the engine's .env, the engine's Python)

The engine books the fee the venue reports on a fill. Alpaca reports none there: its regulatory fees come as separate
FEE activities at the end of the day. Before the engine books those, this shows what the paper account really posts.

Reads ALPACA_API_KEY and ALPACA_SECRET (or ALPACA_API_SECRET) from the engine's secrets file (KAIBOT_ENV_FILE,
default ~/Projects/kaibot-secrets/.env) with the engine's own loader (`kaibot.engine.config.load_env_file`), refuses
any base URL other than the paper endpoint, and sends only GET /v2/account/activities?activity_types=FEE for the last
90 days. Prints the number of entries, their total, and for up to 3 of them (newest first) the field names with
their types and the amounts. Never prints a key, and never an activity id or an account number.
Exit 0 when the read worked (also with no entries), 2 when it failed.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kaibot.engine.config import PAPER_TRADING_URL, load_env_file  # noqa: E402

DEFAULT_ENV = Path.home() / "Projects" / "kaibot-secrets" / ".env"
DAYS, PAGE, MAX_PAGES, SHOWN = 90, 100, 50, 3
PATH = "/v2/account/activities"
VALUES = ("activity_type", "date", "symbol", "qty", "per_share_amount", "net_amount", "status", "description")


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


def fee_activities(http, base: str, now: datetime) -> tuple[list[dict], bool]:
    """Every FEE activity of the last DAYS days, newest first, and whether the page limit cut the list short."""
    params = {"activity_types": "FEE", "direction": "desc", "page_size": PAGE,
              "after": (now - timedelta(days=DAYS)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    out: list[dict] = []
    for _ in range(MAX_PAGES):
        rows = http.call("GET", f"{base}{PATH}", dict(params)) or []
        out.extend(r for r in rows if isinstance(r, dict))
        if len(rows) < PAGE:
            return out, False
        params["page_token"] = rows[-1]["id"]
    return out, True


def _amount(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def report(rows: list[dict], cut: bool) -> list[str]:
    lines = [f"FEE activities on the paper account, last {DAYS} days: {len(rows)}"
             + (f" (stopped after {MAX_PAGES} pages: there are more)" if cut else "")]
    if not rows:
        lines.append("None posted. Either the paper account charges no regulatory fees, or there was no sale in "
                     "these days.")
        return lines
    amounts = [a for a in (_amount(r.get("net_amount")) for r in rows) if a is not None]
    dates = sorted(str(r.get("date")) for r in rows if r.get("date"))
    lines.append(f"net_amount: total {sum(amounts):.2f} over {len(amounts)} entries"
                 + (f", from {dates[0]} to {dates[-1]}" if dates else ""))
    for n, r in enumerate(rows[:SHOWN], 1):
        lines.append(f"entry {n} of {min(len(rows), SHOWN)} shown (newest first)")
        lines.append("  fields: " + ", ".join(f"{k} ({type(v).__name__})" for k, v in sorted(r.items())))
        for k in VALUES:
            if k in r:
                lines.append(f"  {k} = {str(r[k])[:120]}")
    return lines


def main(argv: list[str] | None = None, http=None, now: datetime | None = None) -> int:
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
        rows, cut = fee_activities(http, base, now or datetime.now(UTC))
    except Exception as e:  # an HTTP error: its status only (the adapter's message carries no key)
        print(f"FAILED: {type(e).__name__} {getattr(e, 'status', '')}".rstrip())
        return 2
    print("\n".join(report(rows, cut)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
