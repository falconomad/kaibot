#!/usr/bin/env python3
"""Screen a research-only test group (S66): 60-day beta vs SPY, ADV, median spread, price, first bar, PASS/FAIL.

Dry run by default: prints the table and changes nothing. With --write the passing names go into the
`test_groups: <group>` block of config/universe_seed.yaml, which the live engine does not read.
Data: Alpaca SIP history >= 15 min old, shared `alpaca` budget (config/sources.yaml). Never prints key values.

On the Mac, from the repo root:
    KAIBOT_ENV_FILE=~/Projects/kaibot-secrets/.env .venv/bin/python scripts/screen_test_group.py
    KAIBOT_ENV_FILE=~/Projects/kaibot-secrets/.env .venv/bin/python scripts/screen_test_group.py --write
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.ingest.certs import CERT_HINT, ensure_ca_bundle  # noqa: E402

DEFAULT_ENV = "~/Projects/kaibot-secrets/.env"


def load_env(p: Path) -> dict:
    env = {}
    for line in p.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def load_candidates(path: Path, group: str) -> tuple[str, list[str]]:
    g = yaml.safe_load(path.read_text())["groups"][group]
    syms = [str(c["symbol"]).upper() for c in g["candidates"]]
    if len(set(syms)) != len(syms):
        raise SystemExit(f"duplicate symbols in {path}")
    return g.get("label", group), syms


def main(argv: list[str] | None = None, api=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default=os.environ.get("KAIBOT_ENV_FILE") or DEFAULT_ENV, help=".env with the Alpaca keys")
    ap.add_argument("--group", default="high_beta_growth")
    ap.add_argument("--candidates", default=str(ROOT / "config" / "test_group_candidates.yaml"))
    ap.add_argument("--symbols", default="", help="comma-separated override of the candidates file")
    ap.add_argument("--seed", default=str(ROOT / "config" / "universe_seed.yaml"))
    ap.add_argument("--profiles", default=str(ROOT / "config" / "profiles.yaml"))
    ap.add_argument("--min-sessions", type=int, default=None, help="minimum trading history (default 126 sessions)")
    ap.add_argument("--min-beta", type=float, default=None, help="optional: also fail names below this 60-day beta")
    ap.add_argument("--per-min", type=int, default=None, help="tighter request cap than the shared alpaca budget")
    ap.add_argument("--write", action="store_true", help="write the passing names into the seed's test_groups block")
    a = ap.parse_args(argv)

    from kaibot.common.http import HttpError
    from kaibot.ingest import test_group as tg
    from kaibot.ingest.alpaca_history import Alpaca

    label, symbols = load_candidates(Path(a.candidates), a.group)
    if a.symbols:
        symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    filters = tg.filters_from_profiles(Path(a.profiles), min_sessions=a.min_sessions, min_beta=a.min_beta)
    seed = Path(a.seed)
    cfg = yaml.safe_load(seed.read_text())
    live = {x for v in cfg["sectors"].values() for x in v}
    dup = sorted(live & set(symbols))
    if dup:
        print(f"skipping names already in the live universe: {', '.join(dup)}")
        symbols = [s for s in symbols if s not in live]

    if api is None:
        env_path = Path(a.env).expanduser()
        if not env_path.is_file():
            print(f"No .env found at {env_path} (set KAIBOT_ENV_FILE=/path/to/.env or pass --env)")
            return 2
        env = load_env(env_path)
        key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_API_SECRET") or env.get("ALPACA_SECRET")
        if not key or not secret:
            print("ALPACA_API_KEY and ALPACA_API_SECRET (or ALPACA_SECRET) must be set in the .env")
            return 2
        ensure_ca_bundle()
        api = Alpaca(key, secret, per_min=a.per_min)

    print(f"Screening {len(symbols)} candidates for '{a.group}' (SIP history >= 15 min old; about "
          f"{len(symbols) * 26 // 150 + 1} min at the alpaca budget)", flush=True)
    try:
        rows = tg.screen(api, symbols, filters, market=cfg["market"],
                         progress=lambda i, n, s: print(f"  [{i}/{n}] {s}", file=sys.stderr, flush=True))
    except HttpError as e:
        print(f"Alpaca request failed: HTTP {e.status}." + (f" {CERT_HINT}" if e.status == 0 else ""))
        return 1
    print(tg.format_table(rows))
    passing = [r.symbol for r in rows if r.passed]
    if not a.write:
        print("Dry run: nothing written. Add --write to put the passing names into "
              f"{seed.name} under test_groups.{a.group}.")
        return 0
    if not passing:
        print("No candidate passed: nothing written.")
        return 1
    tg.write_test_group(seed, a.group, passing, label, datetime.now(UTC).date(), filters)
    print(f"Wrote {len(passing)} names to {seed} under test_groups.{a.group} (not read by the live engine).")
    print("Minute bars for replay: .venv/bin/python scripts/fetch_history.py --test-group "
          f"{a.group} --start 2024-01-01 --rth-only --per-min 45   (same KAIBOT_ENV_FILE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
