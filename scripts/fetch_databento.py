"""Download Databento historical data for validation. DRY RUN BY DEFAULT: prints the estimated cost only.

python scripts/fetch_databento.py --symbols AAPL,MSFT --start 2026-10-01 --end 2026-10-02            # cost only
python scripts/fetch_databento.py --symbols AAPL --start 2026-10-01 --end 2026-10-02 --confirm       # download
python scripts/fetch_databento.py --schema bbo-1m --symbols SPY --start 2026-10-01T13:30 --end 2026-10-01T20:00

Every run asks Databento for the price first (metadata.get_cost, free) and prints it. Requests above the cap in
config/sources.yaml (validation.databento.max_cost_usd_per_request, default $10) are refused; --max-cost can only
lower it. Downloads are cached in data/databento/ as Parquet, so the same request is never paid twice.
Exit codes: 0 ok, 2 refused (cost), 1 error. The key is read from the .env file and never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.common.http import HttpError  # noqa: E402
from kaibot.ingest import databento as dbn  # noqa: E402

DEFAULT_ENV = os.environ.get("KAIBOT_ENV_FILE") or str(Path.home() / "Projects" / "kaibot-secrets" / ".env")


def load_env(p: str) -> dict:
    env = {}
    path = Path(p).expanduser()
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=DEFAULT_ENV, help="path to the .env with DATABENTO_API_KEY (or KAIBOT_ENV_FILE)")
    ap.add_argument("--dataset", default="XNAS.ITCH", help="Databento dataset code (default XNAS.ITCH)")
    ap.add_argument("--schema", default=dbn.DEFAULT_SCHEMA, choices=dbn.SCHEMAS)
    ap.add_argument("--symbols", required=True, help="comma-separated, e.g. AAPL,MSFT")
    ap.add_argument("--start", required=True, help="ISO 8601, UTC unless an offset is given (inclusive)")
    ap.add_argument("--end", required=True, help="ISO 8601, exclusive; at least 15 min in the past")
    ap.add_argument("--data", default=str(ROOT / "data"), help="cache root (files go under <data>/databento/)")
    ap.add_argument("--max-cost", type=float, default=None, help="lower the per-request cap for this run (USD)")
    ap.add_argument("--confirm", action="store_true", help="actually download (billed against the credit)")
    a = ap.parse_args(argv)
    try:
        q = dbn.Query(a.dataset, a.schema, tuple(s.strip().upper() for s in a.symbols.split(",") if s.strip()),
                      a.start, a.end)
        policy = dbn.Policy.load()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    key = load_env(a.env).get("DATABENTO_API_KEY") or os.environ.get("DATABENTO_API_KEY")
    if not key:
        print(f"error: DATABENTO_API_KEY not found in {a.env} or the environment", file=sys.stderr)
        return 1
    try:
        dbn.fetch(dbn.Databento(key), q, root=Path(a.data), policy=policy, confirm=a.confirm,
                  max_cost_usd=a.max_cost)
    except dbn.CostRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    except (HttpError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
