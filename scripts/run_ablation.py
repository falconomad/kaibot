"""Alt-data ablation for admission (story S39): do the pre-registered alt-data filters beat random at 95%?

python scripts/run_ablation.py --trades data/reports/trades_balanced_<tag>.csv          # replay trades
python scripts/run_ablation.py --supabase --env ~/Projects/kaibot-secrets/.env [--run-id 3] [--strategy dip_reversal]

Trades come from a replay CSV (run_replay.py `trades_*.csv`: date, entry_m, pnl, risk; run_shadow_replay.py
`shadow_trades_*.csv`: date, m, pnl_usd, risk; or any CSV with symbol, entry_ts, pnl_usd, risk_usd) or from the
Supabase `kaibot.trade` table (read-only selects; SUPABASE_URL and SUPABASE_SERVICE_KEY / SUPABASE_SERVICE_ROLE_KEY
from the env file or the environment, never printed). Features come from kaibot.features.alt.alt_features, point in
time at each trade's decision (entry minus --decision-lag-min). Writes data/ablation/<UTC date>.json and appends a
trial row (kind "ablation") to data/trials.jsonl. Offline except for --supabase. Passing only PROPOSES a feature
(docs/ops/ablation.md).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation.ablation import SESSION_TZ, parse_config, run_ablation  # noqa: E402
from kaibot.features.alt import alt_features  # noqa: E402
from kaibot.ingest.alt import store as alt_store  # noqa: E402

TRADE_COLS = "id,run_id,strategy,symbol,entry_ts,exit_ts,risk_usd,pnl_usd,exit_reason"


def normalize_trades(df: pd.DataFrame, lag_min: float = 1.0) -> pd.DataFrame:
    """Any supported trade table -> symbol, entry_ts, decision_ts, r, pnl_usd (closed trades with risk > 0)."""
    d = df.copy()
    if "entry_ts" in d:
        entry = pd.to_datetime(d["entry_ts"], utc=True)
    elif "date" in d and ("entry_m" in d or "m" in d):
        # replay minutes count from 09:30 ET; run_replay's entry_m is the fill minute, the shadow replay's m is the
        # decision minute (decided at its close, filled at the next open)
        mins = d["entry_m"] if "entry_m" in d else d["m"] + 1
        open_ = pd.to_datetime(d["date"].astype(str)).dt.tz_localize(SESSION_TZ) + pd.Timedelta(hours=9, minutes=30)
        entry = (open_ + pd.to_timedelta(mins.astype(float), unit="m")).dt.tz_convert(UTC)
    else:
        raise SystemExit("trades need entry_ts, or date + entry_m / m")
    pnl = d["pnl_usd"] if "pnl_usd" in d else d["pnl"]
    risk = d["risk_usd"] if "risk_usd" in d else d["risk"]
    pnl, risk = pd.to_numeric(pnl, errors="coerce"), pd.to_numeric(risk, errors="coerce")
    out = pd.DataFrame({"symbol": d["symbol"].astype(str), "entry_ts": entry,
                        "decision_ts": entry - pd.Timedelta(minutes=lag_min), "pnl_usd": pnl, "risk_usd": risk})
    out["r"] = out["pnl_usd"] / out["risk_usd"]
    if "exit_ts" in d:
        out = out[pd.notna(d["exit_ts"]).to_numpy()]
    return out[(out["risk_usd"] > 0) & out["pnl_usd"].notna()].reset_index(drop=True)


def supabase_trades(env: str | None, run_id: int | None, strategy: str | None) -> pd.DataFrame:
    from kaibot.engine.config import DEFAULT_SUPABASE_URL, load_env_file
    from kaibot.engine.store import Store

    load_env_file(env)
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise SystemExit("missing SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE_KEY) in the env file")
    st = Store(os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/"), key)
    params = {"select": TRADE_COLS, "exit_ts": "not.is.null", "order": "id.asc"}
    if run_id is not None:
        params["run_id"] = f"eq.{int(run_id)}"
    if strategy:
        params["strategy"] = f"eq.{strategy}"
    rows, page = [], 1000
    while True:  # read-only GETs, paged
        batch = st.select("trade", {**params, "limit": str(page), "offset": str(len(rows))})
        rows += batch
        if len(batch) < page:
            break
    return pd.DataFrame(rows, columns=TRADE_COLS.split(","))


@contextlib.contextmanager
def cached_alt_reads() -> Iterator[None]:
    """Read each alt source's Parquet once per process instead of once per trade. Point in time is unaffected:
    read_pit still filters available_ts <= decision_ts on every call (the cached frames are never mutated)."""
    orig = alt_store.read_source
    cache: dict = {}

    def cached(root, source, columns=None):
        k = (str(root), source, tuple(columns) if columns else None)
        if k not in cache:
            cache[k] = orig(root, source, columns)
        return cache[k]

    alt_store.read_source = cached
    try:
        yield
    finally:
        alt_store.read_source = orig


def alt_lookup(alt_root: Path):
    return lambda symbol, decision_ts: alt_features(alt_root, symbol, decision_ts)


def git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT,
                             timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--trades", help="replay trades CSV")
    src.add_argument("--supabase", action="store_true", help="read closed trades from kaibot.trade (read-only)")
    ap.add_argument("--env", default=None, help="env file with SUPABASE_* (only for --supabase)")
    ap.add_argument("--run-id", type=int, default=None)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--data", default=str(ROOT / "data"), help="output root (ablation/, trials.jsonl)")
    ap.add_argument("--alt-root", default=None, help="root holding alt/<source>/ (default: --data)")
    ap.add_argument("--config", default=str(ROOT / "config" / "ablation.yaml"))
    ap.add_argument("--decision-lag-min", type=float, default=1.0,
                    help="features are read as of entry minus this many minutes")
    a = ap.parse_args(argv)
    data = Path(a.data)
    alt_root = Path(a.alt_root) if a.alt_root else data
    cfg_path = Path(a.config)
    cfg = parse_config(cfg_path.read_bytes())

    if a.trades:
        raw, source = pd.read_csv(a.trades), f"csv:{Path(a.trades).name}"
    else:
        raw = supabase_trades(a.env, a.run_id, a.strategy)
        source = "supabase:kaibot.trade" + (f" run_id={a.run_id}" if a.run_id is not None else "") + (
            f" strategy={a.strategy}" if a.strategy else "")
    strategies = sorted(raw["strategy"].dropna().astype(str).unique()) if "strategy" in raw else []
    trades = normalize_trades(raw, a.decision_lag_min)

    with cached_alt_reads():
        res = run_ablation(trades, alt_lookup(alt_root), cfg, git_sha=git_sha(), source=source)
    now = datetime.now(UTC)
    res.update({"run_at": now.isoformat(timespec="seconds"), "config": str(cfg_path.name),
                "decision_lag_min": a.decision_lag_min, "trades_input": len(raw),
                "trades_dropped": len(raw) - len(trades), "strategies": strategies,
                "caveats": [
                    "alt data only accrues from M1 onward: early runs are 'insufficient' by design",
                    "one test per rule at 95%: with several rules a false admit somewhere is likely; see p_value and "
                    "bonferroni_pass, and the replay trial the admission PR must carry",
                    "macro GDELT tone is the same for every symbol on a given bucket: trades on one day are not "
                    "independent draws",
                    "admit only proposes the feature; trading stays unchanged until a reviewed PR adds it to "
                    "rules_extra",
                ]})
    out = data / "ablation"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{now.date().isoformat()}.json"
    path.write_text(json.dumps(res, indent=1, allow_nan=False))
    trial_id = f"ablation_{now.strftime('%Y%m%dT%H%M%S')}"
    with (data / "trials.jsonl").open("a") as fh:
        fh.write(json.dumps({
            "ts": res["run_at"], "trial_id": trial_id, "kind": "ablation", "strategy": ",".join(strategies) or None,
            "code_sha": res["git_sha"], "params": f"pre-registered {cfg_path.name} sha256={cfg.sha256[:12]}",
            "config_sha256": cfg.sha256, "source": source, "window": res["window"],
            # every rule actually tested is a filtered variant of the strategy: it counts against the DSR
            "n_trials": res["rules_tested"],
            "metrics": {"trades": res["trades"], "trading_days": res["trading_days"], **res["summary"],
                        "admitted": res["admitted"],
                        "rules": {p["name"]: {"verdict": p["verdict"], "percentile": p["percentile"],
                                              "coverage": p["coverage"]} for p in res["rules"]}},
        }, default=str) + "\n")
    print(json.dumps({k: res[k] for k in ("trades", "trading_days", "window", "summary", "admitted")}, default=str))
    print(f"wrote {path}")
    return res


if __name__ == "__main__":
    main()
