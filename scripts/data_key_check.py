#!/usr/bin/env python3
"""Check the market-data key after its setup (audit 2 7.2, issue #712; docs/ops/secrets.md, "The data key").

    PYTHONPATH=src python3 scripts/data_key_check.py     # data.env, helper.env and (for one comparison) the .env

The ticket's test: "the relay's key cannot place an order" on the real paper account. Alpaca paper keys cannot be
scoped, so the data pair (data.env) must belong to a SECOND paper account. This script, run by hand by the owner,
reports line by line:
- data.env holds the data pair and no trading pair or service key;
- helper.env no longer holds the trading pair (the setup before #712 put it there);
- the data pair signs in, and its account is NOT the trading account (GET /v2/account with each pair: the two
  account ids differ). This is the one place that reads the trading pair from the .env, to compare the ids, and only
  when run by hand; no helper process does;
- the data account holds no position (it is never traded);
- the data pair reads market data (the latest IEX bar of SPY).
It prints no key or secret, and account numbers only as their last four characters. Exit 0 only when every line is OK.
It sends no order: every call is a GET.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.common.secret_files import (  # noqa: E402
    SERVICE_NAMES,
    TRADING_NAMES,
    data_env_path,
    data_keys,
    names_in,
    read_names,
)
from kaibot.engine.config import DATA_URL, PAPER_TRADING_URL  # noqa: E402

DEFAULT_ENV = "~/Projects/kaibot-secrets/.env"
DEFAULT_HELPER_ENV = "~/Projects/kaibot-secrets/helper.env"


def _open(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())  # noqa: S310


def get(url: str, key: str, secret: str, opener=_open):
    """A GET with an Alpaca key pair: (status, json or None). Never raises for an HTTP error."""
    req = urllib.request.Request(url, method="GET", headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                                                             "Accept": "application/json"})
    try:
        with opener(req, timeout=15) as r:
            return int(r.status), json.loads(r.read(1 << 20) or b"null")
    except urllib.error.HTTPError as e:
        return int(e.code), None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def tail(v) -> str:
    return f"...{str(v)[-4:]}" if v else "?"


def check(data_file: str | None, helper_file: str, env_file: str, opener=_open) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    try:
        key, secret = data_keys(data_file)
        out.append((f"data.env ({data_env_path(data_file)}) holds the data pair only", True, "ok"))
    except SystemExit as e:
        return [("data.env holds the data pair only", False, str(e))]
    left = sorted(names_in(helper_file) & set(TRADING_NAMES + SERVICE_NAMES))
    out.append(("helper.env holds no trading pair and no service key", not left,
                "ok" if not left else f"remove {', '.join(left)} from {helper_file}"))
    code, acct = get(f"{PAPER_TRADING_URL}/v2/account", key, secret, opener)
    data_id = (acct or {}).get("id") if isinstance(acct, dict) else None
    out.append(("the data pair signs in (paper)", code == 200 and bool(data_id),
                f"account {tail((acct or {}).get('account_number'))}" if code == 200 else f"HTTP {code}"))
    trading = read_names(env_file, TRADING_NAMES)
    t_key, t_secret = trading.get("ALPACA_API_KEY"), trading.get("ALPACA_SECRET") or trading.get("ALPACA_API_SECRET")
    if not (t_key and t_secret):
        out.append(("the data account is not the trading account", False, f"no trading pair in {env_file}"))
    else:
        code_t, t_acct = get(f"{PAPER_TRADING_URL}/v2/account", t_key, t_secret, opener)
        trade_id = (t_acct or {}).get("id") if isinstance(t_acct, dict) else None
        same = key == t_key or (data_id is not None and data_id == trade_id)
        ok = code_t == 200 and bool(trade_id) and bool(data_id) and not same
        note = ("the SAME account: make the data pair in a second paper account" if same else
                f"trading account {tail((t_acct or {}).get('account_number'))}, data account "
                f"{tail((acct or {}).get('account_number'))}" if ok else f"HTTP {code_t}")
        out.append(("the data account is not the trading account (its key cannot trade the real run)", ok, note))
    code, pos = get(f"{PAPER_TRADING_URL}/v2/positions", key, secret, opener)
    out.append(("the data account holds no position", code == 200 and pos == [],
                "ok" if code == 200 and pos == [] else (f"{len(pos)} held" if isinstance(pos, list)
                                                         else f"HTTP {code}")))
    code, bar = get(f"{DATA_URL}/v2/stocks/SPY/bars/latest?feed=iex", key, secret, opener)
    out.append(("the data pair reads market data (SPY, IEX)", code == 200 and isinstance(bar, dict) and "bar" in bar,
                "ok" if code == 200 else f"HTTP {code}"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-env", default=None)
    ap.add_argument("--helper-env", default=DEFAULT_HELPER_ENV)
    ap.add_argument("--env", default=DEFAULT_ENV)
    a = ap.parse_args(argv)
    rows = check(a.data_env, str(Path(a.helper_env).expanduser()), str(Path(a.env).expanduser()))
    for name, ok, note in rows:
        print(f"{'OK  ' if ok else 'FAIL'} {name} ({note})")
    return 0 if rows and all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
