"""Databento historical bars for validation (S54). DRY RUN BY DEFAULT: prints the estimated cost only.

    python scripts/fetch_databento.py --env .env --symbols SPY,AAPL --start 2026-10-01 --end 2026-10-02
    python scripts/fetch_databento.py --env .env --symbols SPY --start 2026-10-01 --end 2026-10-02 --confirm
    ... --schema bbo-1m      (spreads)      ... --dataset EQUS.MINI (default)

The estimate (metadata.get_cost) always runs and is printed first; anything above the cap in
config/sources.yaml (market_data.databento.max_cost_usd, default $10) is refused. `--max-cost` can only LOWER it.
Every confirmed download is recorded in data/databento/ledger.jsonl; a request that would take the recorded total
past market_data.databento.credit_usd (default $125) is refused, even with --confirm.
Already-downloaded symbols are skipped (data/databento/...parquet), so nothing is paid twice.
Needs `pip install databento` (validation tooling only; never in the live path).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_history import load_env  # noqa: E402  (also puts src/ on sys.path)

from kaibot.ingest import databento_hist as dbh  # noqa: E402
from kaibot.ingest.certs import ensure_ca_bundle  # noqa: E402


def main(argv: list[str] | None = None, client=None) -> int:
    ensure_ca_bundle()  # macOS python.org builds have no root certificates; before any network call
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", required=True)
    ap.add_argument("--symbols", required=True, help="comma-separated, e.g. SPY,AAPL")
    ap.add_argument("--start", required=True, help="ISO date/time, UTC if no offset")
    ap.add_argument("--end", required=True, help="ISO date/time (exclusive), >= 15 min old")
    ap.add_argument("--dataset", default=dbh.DEFAULT_DATASET)
    ap.add_argument("--schema", default=dbh.DEFAULT_SCHEMA, choices=sorted(dbh.SCHEMAS))
    ap.add_argument("--max-cost", type=float, default=None, help="USD; may only lower the sources.yaml cap")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--confirm", action="store_true", help="actually download (spends credit)")
    a = ap.parse_args(argv)

    cfg = dbh.load_config()
    cap = dbh.max_cost_from_config(cfg)
    credit = dbh.credit_from_config(cfg)
    if a.max_cost is not None:
        cap = min(cap, max(0.0, a.max_cost))
    try:
        req = dbh.Request.make(a.symbols.split(","), a.start, a.end, a.dataset, a.schema)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if client is None:
        client = dbh.make_client(load_env(a.env).get("DATABENTO_API_KEY", ""))
    try:
        dbh.fetch(client, req, Path(a.data), cap, confirm=a.confirm, credit_usd=credit)
    except (dbh.CostCapExceeded, ValueError) as e:  # cap, total credit, or an unreadable spend ledger
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
