"""Download replay history: 1-min SIP bars (+ news) for a universe. Resumable: finished months are skipped.

python scripts/fetch_history.py --env .env --start 2025-01-01 [--news]
python scripts/fetch_history.py --env .env --universe config/universe_history.yaml --start 2016-01-01 \
    --rth-only --shard 0/3 --per-min 45      # one of three workers; see scripts/fetch_history.sh
python scripts/fetch_history.py --env .env --test-group high_beta_growth --start 2024-01-01 --rth-only
    # S66: SPY + the research-only `test_groups` block (written by scripts/screen_test_group.py --write)
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.ingest.alpaca_history import Alpaca, fetch_bars, fetch_news, month_ranges  # noqa: E402


def load_env(p: str) -> dict:
    env = {}
    for line in Path(p).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def universe_symbols(cfg: dict, test_group: str = "") -> list[str]:
    """Default: SPY, the sector ETFs and every live symbol. With `test_group`: SPY plus that research-only group."""
    if test_group:
        groups = cfg.get("test_groups") or {}
        if test_group not in groups:
            raise SystemExit(f"no test_groups.{test_group} in the universe file: run screen_test_group.py --write")
        return [cfg["market"], *groups[test_group]["symbols"]]
    return [cfg["market"], *cfg["sectors"].keys(), *[s for v in cfg["sectors"].values() for s in v]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--data", default="data")
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_seed.yaml"))
    ap.add_argument("--test-group", default="", help="download SPY + this test_groups block instead of the universe")
    ap.add_argument("--rth-only", action="store_true", help="keep only 09:30-15:59 ET bars (all the replay reads)")
    ap.add_argument("--shard", default="0/1", help="i/n: this worker takes every n-th symbol starting at i")
    ap.add_argument("--rotate", type=int, default=0, help="start at this index (parallel workers)")
    ap.add_argument("--per-min", type=int, default=60, help="request budget for THIS worker")
    a = ap.parse_args()
    env = load_env(a.env)
    api = Alpaca(env["ALPACA_API_KEY"], env.get("ALPACA_API_SECRET") or env["ALPACA_SECRET"], per_min=a.per_min)
    cfg = yaml.safe_load(Path(a.universe).read_text())
    symbols = universe_symbols(cfg, a.test_group)
    symbols = symbols[a.rotate:] + symbols[:a.rotate]
    k, n_shards = (int(x) for x in a.shard.split("/"))
    symbols = symbols[k::n_shards]
    start = datetime.fromisoformat(a.start).replace(tzinfo=UTC)
    end = datetime.now(UTC) - timedelta(minutes=20)  # SIP is free only when >= 15 min old
    data = Path(a.data)
    for i, sym in enumerate(symbols, 1):
        for s, e, tag in month_ranges(start, end):
            out = data / "bars_1m" / f"symbol={sym}" / f"{tag}.parquet"
            current_month = tag == end.strftime("%Y-%m")
            if (out.exists() or out.with_suffix(".empty").exists()) and not current_month:
                continue
            n = fetch_bars(api, sym, s, e, out, rth_only=a.rth_only)
            print(f"[{i}/{len(symbols)}] {sym} {tag}: {n} bars", flush=True)
        if a.news and sym not in cfg["sectors"] and sym != cfg["market"]:
            # one file per calendar year, so an interrupted download resumes where it stopped
            for year in range(start.year, end.year + 1):
                out = data / "news" / f"symbol={sym}" / f"{year}.parquet"
                if out.exists() and year != end.year:
                    continue
                s = max(start - timedelta(days=2), datetime(year, 1, 1, tzinfo=UTC))
                e = min(end, datetime(year + 1, 1, 1, tzinfo=UTC))
                print(f"[{i}/{len(symbols)}] {sym} news {year}: {fetch_news(api, sym, s, e, out)}", flush=True)

if __name__ == "__main__":
    main()
