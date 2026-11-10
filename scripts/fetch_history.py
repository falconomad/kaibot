"""Download M0 replay history: 1-min SIP bars (+ news) for the seed universe. Resumable.

python scripts/fetch_history.py --env .env --start 2025-01-01 [--news]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kaibot.ingest.alpaca_history import Alpaca, fetch_bars, fetch_news, month_ranges  # noqa: E402


def load_env(p: str) -> dict:
    env = {}
    for line in Path(p).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--data", default="data")
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--rotate", type=int, default=0, help="start at this index (parallel workers)")
    ap.add_argument("--per-min", type=int, default=60, help="request budget for THIS worker")
    a = ap.parse_args()
    env = load_env(a.env)
    api = Alpaca(env["ALPACA_API_KEY"], env.get("ALPACA_API_SECRET") or env["ALPACA_SECRET"], per_min=a.per_min)
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "universe_seed.yaml").read_text())
    symbols = [cfg["market"], *cfg["sectors"].keys(), *[s for v in cfg["sectors"].values() for s in v]]
    symbols = symbols[a.rotate:] + symbols[:a.rotate]
    start = datetime.fromisoformat(a.start).replace(tzinfo=UTC)
    end = datetime.now(UTC) - timedelta(minutes=20)  # SIP is free only when >= 15 min old
    data = Path(a.data)
    for i, sym in enumerate(symbols, 1):
        for s, e, tag in month_ranges(start, end):
            out = data / "bars_1m" / f"symbol={sym}" / f"{tag}.parquet"
            current_month = tag == end.strftime("%Y-%m")
            if out.exists() and not current_month:
                continue
            n = fetch_bars(api, sym, s, e, out)
            print(f"[{i}/{len(symbols)}] {sym} {tag}: {n} bars", flush=True)
        if a.news and sym not in cfg["sectors"] and sym != cfg["market"]:
            out = data / "news" / f"symbol={sym}" / "all.parquet"
            if not out.exists() and not out.with_suffix(".lock").exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                out.with_suffix(".lock").touch()
                print(f"[{i}/{len(symbols)}] {sym} news: {fetch_news(api, sym, start - timedelta(days=2), end, out)}",
                      flush=True)


if __name__ == "__main__":
    main()
