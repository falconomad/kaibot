"""Download replay history: 1-min SIP bars (+ news) for a universe. Resumable: finished months are skipped.

python scripts/fetch_history.py --env .env --start 2025-01-01 [--news]
python scripts/fetch_history.py --env .env --universe config/universe_history.yaml --start 2016-01-01 \
    --rth-only --shard 0/3 --per-min 45      # one of three workers; see scripts/fetch_history.sh

Symbols outside the universe (S66 test group, custom tickers). On the Mac, from the repo root:
    KAIBOT_ENV_FILE=~/Projects/kaibot-secrets/.env .venv/bin/python scripts/fetch_history.py \
        --symbols NBIS,CRWV,FSLY --start 2024-01-01 --rth-only --per-min 45
    KAIBOT_ENV_FILE=~/Projects/kaibot-secrets/.env .venv/bin/python scripts/fetch_history.py \
        --test-group high_beta_growth --start 2024-01-01 --rth-only --per-min 45
--symbols downloads SPY plus exactly the tickers named; --test-group downloads SPY plus the research-only
`test_groups` block (written by scripts/screen_test_group.py --write); both together download the union.
Neither changes a config file: the bars only land under data/bars_1m, which the live engine does not read.
Data: Alpaca SIP history >= 15 min old, shared `alpaca` budget (config/sources.yaml). Never prints key values.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.common.http import HttpError  # noqa: E402
from kaibot.ingest.alpaca_history import (  # noqa: E402
    SIP_DELAY,
    Alpaca,
    fetch_bars,
    fetch_news,
    month_ranges,
    parse_symbols,
)
from kaibot.ingest.certs import CERT_HINT, ensure_ca_bundle  # noqa: E402

DEFAULT_ENV = "~/Projects/kaibot-secrets/.env"
SKIP_STATUSES = (400, 404, 422)  # Alpaca's answers for a symbol it does not know: skip it, keep going


def load_env(p: str | Path) -> dict:
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
            known = f"known groups: {', '.join(groups)}" if groups else "the universe file has no test_groups block"
            raise SystemExit(f"no test group '{test_group}' ({known}). Groups are written by "
                             "scripts/screen_test_group.py --write; use --symbols for a hand-picked list.")
        try:
            return [cfg["market"], *parse_symbols(groups[test_group].get("symbols") or [])]
        except ValueError as e:
            raise SystemExit(f"test group '{test_group}': {e}") from None
    return [cfg["market"], *cfg["sectors"].keys(), *[s for v in cfg["sectors"].values() for s in v]]


def select_symbols(cfg: dict, symbols: str = "", test_group: str = "") -> list[str]:
    """What to download. Nothing given: the universe, unchanged. `symbols` and/or `test_group`: SPY (the replay's
    market reference) plus exactly those names, each once. Stops with a clear message on a bad symbol or group."""
    if not symbols and not test_group:
        return universe_symbols(cfg)
    out = universe_symbols(cfg, test_group) if test_group else [cfg["market"]]
    if symbols:
        try:
            out += parse_symbols(symbols)
        except ValueError as e:
            raise SystemExit(f"--symbols: {e}") from None
    return list(dict.fromkeys(out))


def parse_shard(shard: str) -> tuple[int, int]:
    try:
        k, n = (int(x) for x in shard.split("/"))
    except ValueError:
        raise SystemExit(f"--shard: expected i/n, got {shard!r}") from None
    if not 0 <= k < n:
        raise SystemExit(f"--shard: need 0 <= i < n, got {shard!r}")
    return k, n


def main(argv: list[str] | None = None, api=None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default=os.environ.get("KAIBOT_ENV_FILE") or DEFAULT_ENV, help=".env with the Alpaca keys")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--data", default="data")
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_seed.yaml"))
    ap.add_argument("--symbols", default="", help="comma-separated tickers: download SPY + exactly these "
                                                   "instead of the universe (e.g. NBIS,CRWV)")
    ap.add_argument("--test-group", default="", help="download SPY + this test_groups block instead of the universe")
    ap.add_argument("--rth-only", action="store_true", help="keep only 09:30-15:59 ET bars (all the replay reads)")
    ap.add_argument("--shard", default="0/1", help="i/n: this worker takes every n-th symbol starting at i")
    ap.add_argument("--rotate", type=int, default=0, help="start at this index (parallel workers)")
    ap.add_argument("--per-min", type=int, default=60, help="request budget for THIS worker")
    a = ap.parse_args(argv)

    # everything about the request is checked before the keys are read or anything is sent
    explicit = bool(a.symbols or a.test_group)
    cfg = yaml.safe_load(Path(a.universe).read_text())
    symbols = select_symbols(cfg, a.symbols, a.test_group)
    symbols = symbols[a.rotate:] + symbols[:a.rotate]
    k, n_shards = parse_shard(a.shard)
    symbols = symbols[k::n_shards]
    try:
        start = datetime.fromisoformat(a.start).replace(tzinfo=UTC)
    except ValueError:
        raise SystemExit(f"--start: expected YYYY-MM-DD, got {a.start!r}") from None
    end = (now or datetime.now(UTC)) - SIP_DELAY  # SIP is free only when >= 15 min old
    if start >= end:
        raise SystemExit(f"--start {a.start} is not in the past")
    if a.per_min <= 0:
        raise SystemExit("--per-min must be positive")

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

    data = Path(a.data)
    if explicit:
        print(f"Downloading 1-minute SIP bars for {len(symbols)} symbols ({', '.join(symbols)}), {a.start} -> "
              f"{end:%Y-%m-%d %H:%M} UTC (>= 15 min old), into {data / 'bars_1m'}. Finished months are skipped.",
              flush=True)
    skipped, no_bars = [], []
    for i, sym in enumerate(symbols, 1):
        try:
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
        except HttpError as err:
            if err.status in SKIP_STATUSES:
                print(f"[{i}/{len(symbols)}] {sym}: skipped (HTTP {err.status}: unknown or delisted ticker?)",
                      flush=True)
                skipped.append(sym)
                continue
            print(f"Alpaca request failed at {sym}: HTTP {err.status}. Stopped; run the same command again to "
                  f"resume." + (f" {CERT_HINT}" if err.status == 0 else ""), flush=True)
            return 1
        if not any((data / "bars_1m" / f"symbol={sym}").glob("*.parquet")):
            print(f"[{i}/{len(symbols)}] {sym}: no bars since {a.start} (unknown ticker, or listed later?)",
                  flush=True)
            no_bars.append(sym)
    if explicit:
        ok = len(symbols) - len(skipped) - len(no_bars)
        print(f"Done: {ok} of {len(symbols)} symbols have minute bars under {data / 'bars_1m'}."
              + (f" Without bars: {', '.join(skipped + no_bars)}." if skipped or no_bars else ""), flush=True)
        return 1 if skipped or no_bars else 0
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
