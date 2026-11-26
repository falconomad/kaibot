"""Which secret a helper process may read (audit 2 7.2, issue #712; docs/ops/secrets.md).

Alpaca paper keys cannot be scoped: any key pair of a paper account can read market data AND place orders on that
account. So the helpers that only need market data (the price relay, the ticker-check worker) get a key pair of a
SEPARATE Alpaca paper account, which holds nothing and is never traded: the "data pair", in its own file

    ~/Projects/kaibot-secrets/data.env      (KAIBOT_DATA_ENV_FILE names another file)
    ALPACA_DATA_KEY=<key of the second paper account>
    ALPACA_DATA_SECRET=<its secret>

That pair can place orders only on the empty second account: it cannot see, buy or sell anything of the real paper
account. The trading pair (ALPACA_API_KEY / ALPACA_SECRET) stays in the engine's .env for the engine and its
watchdog only. A data file that holds the trading pair or a Supabase service key is refused.

`read_names` reads an env file line by line and keeps ONLY the names asked for: a name not asked for (the trading
pair in the engine's .env, read by the ticker worker for its Supabase key) is never put in a dict, in os.environ or
in a client. Stdlib only (the helpers' path imports nothing else of the package). Nothing here logs a value.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

DATA_ENV = "KAIBOT_DATA_ENV_FILE"
DEFAULT_DATA_ENV = "~/Projects/kaibot-secrets/data.env"
DATA_NAMES = ("ALPACA_DATA_KEY", "ALPACA_DATA_SECRET")
# what a data file may never hold: the real paper account's trading pair and the Supabase service key
TRADING_NAMES = ("ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET")
SERVICE_NAMES = ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY")


def _lines(p: Path):
    for line in p.read_text().splitlines():
        s = line.strip()
        if "=" not in s or s.startswith("#"):
            continue
        k, v = s.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        yield k, v


def _value(v: str) -> str:
    return v.split(" #")[0].strip().strip('"').strip("'")


def read_names(path: str | Path, names: Iterable[str]) -> dict[str, str]:
    """The listed names of an env file (KEY=VALUE, " #" starts a comment, quotes off), nothing else. A value of a
    name not listed is skipped before it is parsed. A missing file: {}."""
    want = set(names)
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        return {}
    return {k: _value(v) for k, v in _lines(p) if k in want and _value(v)}


def names_in(path: str | Path) -> set[str]:
    """The names an env file sets (no values): for the checks that a file does not hold what it must not."""
    p = Path(os.path.expanduser(str(path)))
    return {k for k, v in _lines(p) if _value(v)} if p.exists() else set()


def data_env_path(path: str | None = None) -> Path:
    return Path(os.path.expanduser(path or os.environ.get(DATA_ENV) or DEFAULT_DATA_ENV))


def data_keys(path: str | None = None) -> tuple[str, str]:
    """(key, secret) of the data pair, or SystemExit with what to do. Refuses a data file that holds the trading pair
    or a service key: the point of the file is that the process reading it cannot have them."""
    p = data_env_path(path)
    if not p.exists():
        raise SystemExit(f"no market-data key file {p}: the helpers no longer use the trading key "
                         "(docs/ops/secrets.md, 'The data key')")
    bad = names_in(p) & set(TRADING_NAMES + SERVICE_NAMES)
    if bad:
        raise SystemExit(f"{p} holds {', '.join(sorted(bad))}: remove it (the data file holds only the data pair)")
    env = read_names(p, DATA_NAMES)
    missing = [n for n in DATA_NAMES if not env.get(n)]
    if missing:
        raise SystemExit(f"{p}: missing {', '.join(missing)} (docs/ops/secrets.md)")
    return env["ALPACA_DATA_KEY"], env["ALPACA_DATA_SECRET"]
