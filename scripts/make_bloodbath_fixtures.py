"""Record golden outputs from bloodbath's own code for the S02 parity test. Run once per bloodbath change:

python -m venv /tmp/bb && /tmp/bb/bin/pip install alpaca-py
ALPACA_API_KEY=x ALPACA_API_SECRET=x GEMINI_API_KEY=x \\
  /tmp/bb/bin/python scripts/make_bloodbath_fixtures.py --bloodbath ../bloodbath

No network: the movers case feeds a recorded-shape screener response through alpaca-py's `Movers` model and
bloodbath's get_top_movers with the client stubbed. Writes tests/fixtures/bloodbath_parity.json.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bars(rng: random.Random, n: int, price: float = 100.0, zero_rate: float = 0.0) -> list[dict]:
    out, p = [], price
    for _ in range(n):
        p *= 1 + rng.gauss(0, 0.02)
        c = 0.0 if rng.random() < zero_rate else round(p, rng.choice([2, 4]))
        v = float(rng.choice([0, rng.randint(1, 10_000), rng.randint(100_000, 90_000_000)]))
        out.append({"c": c, "v": v, "t": "2026-01-01T05:00:00+00:00"})
    return out


def cases(seed: int = 20261110) -> list[dict]:
    rng = random.Random(seed)
    edge = [
        {"history": [], "spy": []}, {"history": bars(rng, 1), "spy": bars(rng, 5)},
        {"history": [{"c": 0.0, "v": 1.0}, {"c": 0.0, "v": 2.0}], "spy": bars(rng, 5)},
        {"history": [{"c": 10.0, "v": 0.0}] * 5, "spy": []},  # flat, zero volume
        {"history": bars(rng, 2), "spy": None}, {"history": bars(rng, 3), "spy": bars(rng, 1)},
        {"history": [{"c": 10, "v": 5}, {"c": 11, "v": 6}, {"c": 12, "v": 7}], "spy": [{"c": 1}, {"c": 1.0}]},
    ]
    rand = []
    for _ in range(300):
        n = rng.choice([2, 3, 4, 5, 5, 5, 6, 8])
        news = [{"source": rng.choice(["benzinga", "Benzinga", "reuters", " ", "", "prn"])}
                for _ in range(rng.randint(0, 5))]
        rand.append({"history": bars(rng, n, rng.uniform(3, 900), zero_rate=0.05),
                     "spy": bars(rng, rng.choice([0, 2, 3, 5]), 500), "news": news})
    return edge + rand


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bloodbath", required=True)
    a = ap.parse_args()
    bb = Path(a.bloodbath).resolve()
    sys.path.insert(0, str(bb))
    from ai_engines.candidate_ranker import build_features, detect_market_regime
    from ai_engines.technical_analyst import evaluate_technicals
    from alpaca.data.models.screener import Movers
    from data_ingestion import market_data

    out = {"bloodbath_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=bb, capture_output=True,
                                              text=True).stdout.strip(), "indicators": [], "movers": []}
    for c in cases():
        out["indicators"].append({
            **c,
            "technical": evaluate_technicals("X", c["history"], c["spy"]),
            "regime": detect_market_regime(c["spy"] or []),
            "features": build_features("X", c["history"], c["spy"] or [], c.get("news")),
        })
    raw_cases = [
        {"gainers": [{"symbol": "ABCD", "percent_change": 41.2, "change": 3.1, "price": 10.6},
                     {"symbol": "EFG", "percent_change": 12, "change": 1, "price": 9}],
         "losers": [{"symbol": "HIJ", "percent_change": -22.75, "change": -4.4, "price": 15.01}],
         "market_type": "stocks", "last_updated": "2026-11-10T15:59:00.123456789Z"},
        {"gainers": [], "losers": [], "market_type": "stocks", "last_updated": "2026-11-10T15:59:00Z"},
    ]
    for raw in raw_cases:
        class Stub:
            def get_market_movers(self, req, _raw=raw):
                return Movers(**_raw)
        market_data.screener_client = Stub()
        out["movers"].append({"raw": raw, "expected": market_data.get_top_movers(limit=15)})
    f = ROOT / "tests" / "fixtures" / "bloodbath_parity.json"
    f.write_text(json.dumps(out, indent=0, sort_keys=True))
    print(f"wrote {f}: {len(out['indicators'])} indicator cases, {len(out['movers'])} movers cases")


if __name__ == "__main__":
    main()
