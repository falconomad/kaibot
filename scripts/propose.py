"""Intake of one proposal for the counted proposer (M12 ticket 3.10, issue #472; docs/research/proposer/hurdle.md).

    python scripts/propose.py --formula "rank(ret(252, 21)) - rank(vol(63))" --family cross_sectional_momentum \\
        --proposer model:<pinned id> --why "momentum, less its volatility"

Parses and checks the formula (`evaluation.proposals`), enforces three machine proposals an ISO week, appends it to
docs/research/proposer/proposals.jsonl (append-only; committed by pull request), and writes one trial row
(`variant: proposal`, n_trials 1, duplicates included) to the registry. Never runs a backtest; never trades.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.evaluation import gate_v3 as g3  # noqa: E402
from kaibot.evaluation import proposals as pr  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402

LEDGER = ROOT / "docs" / "research" / "proposer" / "proposals.jsonl"


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--formula", required=True)
    ap.add_argument("--family", required=True)
    ap.add_argument("--proposer", required=True, help="model:<pinned model id> or human:<name>")
    ap.add_argument("--why", default="")
    ap.add_argument("--date", default=None, help="the proposal's date (default today)")
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--trials", default=str(ROOT / "data" / "trials.jsonl"), help="the registry file ('' = none)")
    a = ap.parse_args(argv)
    if not (a.proposer.startswith("model:") or a.proposer.startswith("human:")) or len(a.proposer) < 7:
        raise SystemExit("propose: --proposer is model:<pinned model id> or human:<name>")
    if a.family not in g3.GateV3.load().families:
        raise SystemExit(f"propose: family {a.family!r} is not in config/gate_v3.yaml")
    on = date.fromisoformat(a.date) if a.date else date.today()
    try:
        row = pr.intake(a.ledger, a.formula, proposer=a.proposer, family=a.family, on=on, why=a.why)
    except pr.FormulaError as e:
        raise SystemExit(f"propose: refused: {e}") from None
    if a.trials:
        trials_mod.append(a.trials, {"trial_id": f"proposal_{row['id']}", "strategy": f"proposal_{row['id']}",
                                     "variant": "proposal", "proposal_id": row["id"], "family": a.family,
                                     "n_trials": 1, "params": {"formula": row["canonical"]},
                                     "duplicate_of": row["duplicate_of"], "proposer": a.proposer})
    print(f"proposal {row['id']} accepted: {row['canonical']}" + (f" (duplicate of {row['duplicate_of']}, counted)"
                                                                  if row["duplicate_of"] else ""))
    return row


if __name__ == "__main__":
    main()
