#!/usr/bin/env python3
"""M5 S48: process the config replay queue (kaibot.replay_job). Each applied Strategy config is replayed on the design
window and logs a trial row (data/trials.jsonl and kaibot.trial), so it counts against the deflated Sharpe like
any other parameter change. Run it on the machine that has the minute-bar history:

    python scripts/run_config_replays.py [--env ~/Projects/kaibot-secrets/.env] [--data data]

Without the history (M0 #3 not done yet) jobs are marked waiting_for_data and retried on the next run."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.engine.userconfig import ConfigError, tighten  # noqa: E402
from kaibot.replay.config_replay import DESIGN, NoData, replay_config  # noqa: E402
from kaibot.risk.profiles import load_profiles, load_strategies  # noqa: E402

log = logging.getLogger("config_replays")


def process(store, data_dir: Path, *, replay=replay_config, now=None) -> list[dict]:
    """Run every queued or waiting job once. Returns what happened per job. Never raises for one bad job."""
    now = now or (lambda: datetime.now(UTC).isoformat())
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    cal = yaml.safe_load((ROOT / "config" / "macro_calendar.yaml").read_text())
    import numpy as np

    fomc = {np.datetime64(str(d), "D") for d in cal.get("fomc_statement_1400", [])}
    uni = frozenset(s for v in cfg["sectors"].values() for s in v)
    themes = {k: v["symbols"] for k, v in (cfg.get("themes") or {}).items()}
    profiles, strats = load_profiles(), load_strategies()
    out = []
    jobs = [j for j in store.select("replay_job", {"status": "in.(queued,waiting_for_data)", "order": "id.asc"})]
    for j in jobs:
        key = {"id": j["id"]}
        try:
            run = store.select("run", {"id": f"eq.{j['run_id']}"})[0]
            c = store.select("config", {"run_id": f"eq.{j['run_id']}", "version": f"eq.{j['config_version']}"})[0]
            level = run.get("level") or "balanced"
            rules = tighten(profiles[level], strats[level], c["body"], version=int(c["version"]), universe=uni,
                            themes=themes)
            store.update("replay_job", key, {"status": "running", "updated_at": now()})
            res = replay(rules, cfg, data_dir, fomc)
        except NoData as e:
            store.update("replay_job", key, {"status": "waiting_for_data", "result": {"reason": str(e)},
                                             "updated_at": now()})
            out.append({"id": j["id"], "status": "waiting_for_data"})
            continue
        except (ConfigError, IndexError, KeyError) as e:
            store.update("replay_job", key, {"status": "error", "result": {"reason": f"{type(e).__name__}: {e}"[:300]},
                                             "updated_at": now()})
            out.append({"id": j["id"], "status": "error"})
            continue
        except Exception as e:  # noqa: BLE001 - one broken replay must not stop the queue
            log.exception("replay failed")
            store.update("replay_job", key, {"status": "error", "result": {"reason": type(e).__name__},
                                             "updated_at": now()})
            out.append({"id": j["id"], "status": "error"})
            continue
        trial = {"strategy": "dip_reversal", "params": {"kind": "config", "run_id": j["run_id"],
                                                       "config_version": j["config_version"], "body": c["body"]},
                 "code_sha": os.environ.get("KAIBOT_CODE_SHA", "unknown"), "window_start": DESIGN[0],
                 "window_end": DESIGN[1], "metrics": {k: res[k] for k in ("trades", "net_return_pct",
                                                                          "t_stat_per_trade", "daily_sharpe_ann")}}
        store.insert("trial", trial, returning=False)
        trials = data_dir / "trials.jsonl"
        trials.parent.mkdir(parents=True, exist_ok=True)
        with trials.open("a") as fh:
            fh.write(json.dumps({"ts": now(), "trial_id": f"config_r{j['run_id']}_v{j['config_version']}", **trial},
                                default=str) + "\n")
        store.update("replay_job", key, {"status": "done", "result": res, "updated_at": now()})
        out.append({"id": j["id"], "status": "done", "trades": res["trades"]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=os.path.expanduser("~/Projects/kaibot-secrets/.env"))
    ap.add_argument("--data", default=str(ROOT / "data"))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    env = {}
    if Path(a.env).exists():
        for line in Path(a.env).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    env = {**env, **os.environ}
    from kaibot.engine.store import Store

    key = env.get("SUPABASE_SERVICE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY")
    store = Store(env.get("SUPABASE_URL", "https://dosdkdlxejqynbgkjhup.supabase.co"), key)
    for r in process(store, Path(a.data)):
        print(json.dumps(r))


if __name__ == "__main__":
    main()
