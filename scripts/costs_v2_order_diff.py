"""#559: the order diff of cost model v2 against v1 on the engine's daily path. Research only.

    python scripts/costs_v2_order_diff.py --fixture                         # the committed fixture (seconds)
    python scripts/costs_v2_order_diff.py --data data --end 2024-12-31      # trend_momentum on the store (Mac)

Runs one rule twice through `engine.daily.run_daily` with `SimBroker`, identical but for `cost_model`, and writes
which orders differ and by how much: the first decision must be identical; later ones may differ only in share
counts that follow the equity the two models leave (the same names bought and sold). Prints one table and exits 1
when the first decision differs or the names traded overlap by less than 90%.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.engine import daily_report as dr  # noqa: E402
from kaibot.engine.daily import DailyData, run_daily, sessions_window  # noqa: E402
from kaibot.evaluation import holdout  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402
from kaibot.strategies import momentum as mom  # noqa: E402


def diff(data: DailyData, version: str, i0: int, i1: int, level: str = "balanced") -> dict:
    pol = load_profiles()[level]
    r = {k: run_daily(mom.VERSIONS[version], data, pol, i0, i1, cost_model=k) for k in ("v1", "v2")}
    orders = {k: {(i, kind, s): q for i, kind, s, q in v.orders} for k, v in r.items()}
    first = min(i for i, *_ in r["v1"].orders) if r["v1"].orders else None
    same_first = first is None or ({k: q for k, q in orders["v1"].items() if k[0] == first}
                                   == {k: q for k, q in orders["v2"].items() if k[0] == first})
    keys1, keys2 = set(orders["v1"]), set(orders["v2"])
    overlap = len(keys1 & keys2) / max(len(keys1 | keys2), 1)
    shares = [abs(float(orders["v2"][k]) - float(orders["v1"][k])) for k in keys1 & keys2
              if isinstance(orders["v1"][k], int | float) and isinstance(orders["v2"][k], int | float)]
    cost = {k: sum(row["cost"] for row in v.book.daily) for k, v in r.items()}
    return {"version": version, "sessions": int(i1 - i0 + 1), "orders": {k: len(v) for k, v in orders.items()},
            "first_decision_identical": bool(same_first), "names_overlap": overlap,
            "only_in_v1": len(keys1 - keys2), "only_in_v2": len(keys2 - keys1),
            "mean_share_difference": float(np.mean(shares)) if shares else 0.0,
            "costs_paid": cost, "final_equity": {k: v.book.equity for k, v in r.items()},
            "ok": bool(same_first and overlap >= 0.9)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="data")
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--version", default="trend_momentum", choices=list(mom.VERSIONS))
    ap.add_argument("--universe", default="pit", choices=("pit", "today"))
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--out", default=None)
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    holdout.enforce(script=__file__, end=a.end, rule=mom.VERSIONS[a.version].rule, opening=a.open_holdout,
                    data=a.data)
    if a.fixture:
        market, info = dr.load_fixture(dr.FIXTURE)
        w0 = info["window"][0]
    else:
        market, info = dr.load_store(Path(a.data), Path(a.universe_file), a.universe, a.start, a.end)
        w0 = "2017-05-01"
    data = DailyData.from_market(market)
    i0, i1 = sessions_window(data, w0, a.end)
    rep = diff(data, a.version, i0, i1)
    text = json.dumps(rep, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
