"""Quoted-spread calibration for cost model v2 (#111, Data part). OFFLINE: no keys, no network, no spending.

    python scripts/fetch_spread_calibration.py plan               sample per v2 ADV bucket + dry-run fetch commands
    python scripts/fetch_spread_calibration.py plan --sessions 3 --per-bucket 5 --exclude SPY,QQQ
    python scripts/fetch_spread_calibration.py report             measure the cached bbo-1m minutes against v2

`plan` reads the daily bar store (<data>/daily, scripts/fetch_daily.sh), takes the last `--sessions` sessions in it
as the calibration window, computes each key's 20-session ADV before that window, picks `--per-bucket` symbols per
bucket and writes <data>/databento/calibration/sample.csv. It prints one `fetch_databento.py --schema bbo-1m`
command per session, regular hours only. Those commands are DRY RUNS: run them, read the estimate, and add
--confirm only if it fits (fetch_databento.py enforces the per-request cap and the total credit in
config/sources.yaml). `report` reads only the Parquet cache and writes spreads_by_symbol.csv,
spreads_by_bucket.csv and calibration.txt next to sample.csv. Nothing here changes cost model v2.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.broker.costs_v2 import V2Params  # noqa: E402  (research model; read only)
from kaibot.ingest import nyse_sessions as nyse  # noqa: E402
from kaibot.ingest import spread_calibration as sc  # noqa: E402
from kaibot.ingest.daily_store import DailyStore  # noqa: E402


def v2_table(p: V2Params | None = None) -> sc.SpreadTable:
    """Cost model v2's half-spread table as the calibration reads it."""
    p = p or V2Params()
    return sc.SpreadTable(p.buckets, p.half_bp_floor, p.tick_half, p.open_mult, p.close_mult)


def _out_dir(data: Path) -> Path:
    return data / "databento" / "calibration"


def plan(a: argparse.Namespace) -> int:
    data = Path(a.data)
    store = DailyStore(data / "daily")
    keys = store.keys()
    if not keys:
        print(f"error: no daily bars in {data / 'daily'} (run scripts/fetch_daily.sh first)", file=sys.stderr)
        return 2
    ends = [store.bars(k)["date"].max() for k in (["SPY"] if "SPY" in keys else keys)]
    ends = [e for e in ends if not pd.isna(e)]
    if not ends:
        print("error: the daily store has no bars", file=sys.stderr)
        return 2
    last = pd.Timestamp(max(ends)).date()
    days = nyse.sessions(last - timedelta(days=10 * a.sessions + 10), last)[-a.sessions:]
    table = v2_table()
    advs = sc.adv_table(store, days[0], table, keys)
    sample = sc.plan_sample(advs, table, a.per_bucket, [s for s in a.exclude.split(",") if s])
    out = _out_dir(data)
    out.mkdir(parents=True, exist_ok=True)
    sample.to_csv(out / "sample.csv", index=False, float_format="%.0f")
    print(f"window: {days[0]} .. {days[-1]} ({len(days)} sessions); ADV over the {sc.ADV_SESSIONS} sessions before")
    for label in sc.bucket_labels(table):
        b = sample[sample["bucket"] == label]
        names = ", ".join(f"{r.symbol} (${r.adv_usd / 1e6:,.0f}M)" for r in b.itertuples(index=False))
        print(f"  {label:>10}: {names or '-'}")
    print(f"sample -> {out / 'sample.csv'}")
    if len(sample) == 0:
        return 1
    print("\nDry runs (cost estimate only; add --confirm after reading it):")
    for cmd in sc.fetch_commands(sample["symbol"].tolist(), days, a.env):
        print("  " + cmd)
    return 0


def report(a: argparse.Namespace) -> int:
    data = Path(a.data)
    sample_csv = _out_dir(data) / "sample.csv"
    advs = pd.read_csv(sample_csv) if sample_csv.exists() else None
    symbols = [s for s in a.symbols.split(",") if s] or None
    cal = sc.calibrate(data, v2_table(), advs, symbols)
    paths = cal.write(_out_dir(data))
    print(cal.text(), end="")
    print("written: " + ", ".join(str(p) for p in paths))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="pick the sample and print dry-run fetch commands")
    p.add_argument("--sessions", type=int, default=3, help="calibration window: the last N sessions in the store")
    p.add_argument("--per-bucket", type=int, default=4)
    p.add_argument("--exclude", default="", help="comma-separated symbols to leave out (e.g. ETFs)")
    p.add_argument("--env", default=".env", help="printed into the fetch commands only; never read here")
    r = sub.add_parser("report", help="measure cached bbo-1m minutes against cost model v2")
    r.add_argument("--symbols", default="", help="comma-separated; default: every cached bbo-1m symbol")
    a = ap.parse_args(argv)
    if a.cmd == "plan" and (a.sessions < 1 or a.per_bucket < 1):
        print("error: --sessions and --per-bucket must be at least 1", file=sys.stderr)
        return 2
    return plan(a) if a.cmd == "plan" else report(a)


if __name__ == "__main__":
    raise SystemExit(main())
