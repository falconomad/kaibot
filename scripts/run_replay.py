"""M0 replay: no-news dip reversal (rules-only) vs random entries and SPY, after shadow costs.

python scripts/run_replay.py --env .env [--level balanced] [--runs 200] [--start 2025-02-01]

The replay itself lives in src/kaibot/replay/runner.py (same flags, same output); this file only starts it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.replay.runner import load_env, main, vix_prev_close  # noqa: E402

__all__ = ["load_env", "main", "vix_prev_close"]  # scripts/walk_forward.py imports the two helpers from here

if __name__ == "__main__":
    main()
