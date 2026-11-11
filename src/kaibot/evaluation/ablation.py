"""Alt-data ablation for admission (story S39). Pure: no file, network or clock access.

PRD v2: alt data is logged from M1 and traded only after a 3-month ablation. A pre-registered rule is a simple keep
filter on one point-in-time feature (config/ablation.yaml), e.g. `short_volume.svr_rel <= 1.5`. For each rule:

- the kept subset is every trade whose value passes, plus every trade whose value is unknown (unknown = kept);
- its total R is compared with `n_random` (>= 1,000) random subsets of the SAME size drawn from all trades, with a
  seeded numpy RNG (vectorised, deterministic); percentile = random_entry_percentile(kept total R, random totals);
- verdict: "insufficient" with < 63 distinct trading days of known feature values or < 100 trades with a known
  value; "admit" if percentile >= 95 AND kept mean R > all-trades mean R; otherwise "reject".

Under the null (the feature says nothing about R) the kept subset is just a random subset, so the percentile is
~uniform and a single rule is falsely admitted ~5% of the time. "admit" only PROPOSES the feature for rules_extra
(docs/ops/ablation.md); nothing here or in the engine turns it on.

The feature lookup `(symbol, decision_ts) -> dict` is injected. It must be point-in-time (only rows with
available_ts <= decision_ts); scripts/run_ablation.py uses kaibot.features.alt.alt_features, which is.
"""

from __future__ import annotations

import hashlib
import math
import operator
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import yaml

from kaibot.evaluation.metrics import random_entry_percentile, t_stat

# Fixed admission bar (acceptance of S39). The config cannot loosen these.
ADMIT_PERCENTILE = 95.0
MIN_HISTORY_DAYS = 63  # ~3 months of trading days
MIN_KNOWN_TRADES = 100
MIN_RANDOM = 1000
SESSION_TZ = "America/New_York"

OPS: dict[str, Callable] = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt,
                            "==": operator.eq, "!=": operator.ne}

Lookup = Callable[[str, datetime], Mapping]


@dataclass(frozen=True)
class Rule:
    name: str
    feature: str  # dotted path into the lookup's dict, e.g. "short_volume.svr_rel"
    op: str
    value: float
    hypothesis: str = ""

    def keeps(self, v: np.ndarray) -> np.ndarray:
        return OPS[self.op](v, self.value)


@dataclass(frozen=True)
class AblationConfig:
    rules: tuple[Rule, ...]
    seed: int
    n_random: int
    sha256: str


def parse_config(text: str | bytes) -> AblationConfig:
    raw = text.encode() if isinstance(text, str) else text
    cfg = yaml.safe_load(raw) or {}
    n_random = int(cfg.get("n_random", MIN_RANDOM))
    if n_random < MIN_RANDOM:
        raise ValueError(f"n_random must be >= {MIN_RANDOM}")
    rules, names = [], set()
    for r in cfg.get("rules") or []:
        rule = Rule(str(r["name"]), str(r["feature"]), str(r["op"]), float(r["value"]),
                    " ".join(str(r.get("hypothesis", "")).split()))
        if rule.op not in OPS:
            raise ValueError(f"rule {rule.name}: unknown op {rule.op!r}")
        if rule.name in names:
            raise ValueError(f"duplicate rule name {rule.name}")
        if not math.isfinite(rule.value):
            raise ValueError(f"rule {rule.name}: value must be finite")
        names.add(rule.name)
        rules.append(rule)
    return AblationConfig(tuple(rules), int(cfg.get("seed", 0)), n_random, hashlib.sha256(raw).hexdigest())


def feature_value(features: Mapping | None, path: str) -> float | None:
    """Resolve "a.b" in a nested dict to a finite float, else None (missing, None, NaN, non-numeric)."""
    cur = features
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    if isinstance(cur, bool):
        return float(cur)
    try:
        x = float(cur)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def random_subset_totals(r: np.ndarray, k: int, n_random: int, rng: np.random.Generator) -> np.ndarray:
    """Total R of `n_random` subsets of size k drawn without replacement from r (vectorised)."""
    n = r.size
    if k <= 0:
        return np.zeros(n_random)
    if k >= n:
        return np.full(n_random, float(r.sum()))
    keys = rng.random((n_random, n))
    idx = np.argpartition(keys, k - 1, axis=1)[:, :k]
    return r[idx].sum(axis=1)


def _num(x: float | None, nd: int = 6) -> float | None:
    if x is None or not math.isfinite(float(x)):
        return None
    return round(float(x), nd)


def _prepare(trades: pd.DataFrame) -> pd.DataFrame:
    for c in ("symbol", "entry_ts", "r"):
        if c not in trades.columns:
            raise ValueError(f"trades need column {c!r}")
    t = trades.copy()
    t["entry_ts"] = pd.to_datetime(t["entry_ts"], utc=True)
    t["decision_ts"] = pd.to_datetime(t["decision_ts"], utc=True) if "decision_ts" in t else t["entry_ts"]
    if (t["decision_ts"] > t["entry_ts"]).any():
        raise ValueError("decision_ts after entry_ts")
    t["r"] = pd.to_numeric(t["r"], errors="coerce")
    t = t[np.isfinite(t["r"].to_numpy(dtype=float))]
    t = t.sort_values(["decision_ts", "symbol"], kind="stable").reset_index(drop=True)
    t["session"] = t["entry_ts"].dt.tz_convert(SESSION_TZ).dt.date
    return t


def run_ablation(trades: pd.DataFrame, lookup: Lookup, config: AblationConfig, git_sha: str | None = None,
                 source: str = "") -> dict:
    """Evaluate every rule in `config` on `trades` (columns symbol, entry_ts, r; optional decision_ts, pnl_usd).

    `lookup(symbol, decision_ts)` is called once per distinct (symbol, decision_ts); decision_ts is a tz-aware UTC
    datetime never later than the entry. Returns a JSON-serialisable dict (no NaN).
    """
    t = _prepare(trades)
    n = len(t)
    r = t["r"].to_numpy(dtype=float)
    feats: dict[tuple[str, pd.Timestamp], Mapping] = {}
    lookup_errors = 0
    for sym, ts in t[["symbol", "decision_ts"]].drop_duplicates().itertuples(index=False, name=None):
        try:
            feats[(sym, ts)] = lookup(str(sym), ts.to_pydatetime()) or {}
        except Exception:  # a broken reader makes the value unknown (kept), never a crash
            lookup_errors += 1
            feats[(sym, ts)] = {}
    rows = [feats[(s, ts)] for s, ts in zip(t["symbol"], t["decision_ts"], strict=True)]
    all_mean = float(r.mean()) if n else 0.0

    per_rule = []
    for i, rule in enumerate(config.rules):
        v = np.array([np.nan if (x := feature_value(f, rule.feature)) is None else x for f in rows], dtype=float)
        known = ~np.isnan(v)
        with np.errstate(invalid="ignore"):
            passes = known & rule.keeps(np.where(known, v, 0.0))
        keep = ~known | passes
        n_known, n_kept = int(known.sum()), int(keep.sum())
        days = int(t.loc[known, "session"].nunique()) if n_known else 0
        kept_r = r[keep]
        kept_total = float(kept_r.sum())
        kept_mean = float(kept_r.mean()) if n_kept else None
        rng = np.random.default_rng([config.seed, i])
        rand = random_subset_totals(r, n_kept, config.n_random, rng)
        pct = random_entry_percentile(kept_total, rand)
        p_value = float((1 + (rand >= kept_total).sum()) / (1 + rand.size))
        if days < MIN_HISTORY_DAYS or n_known < MIN_KNOWN_TRADES:
            verdict = "insufficient"
        elif pct >= ADMIT_PERCENTILE and kept_mean is not None and kept_mean > all_mean:
            verdict = "admit"
        else:
            verdict = "reject"
        per_rule.append({
            **asdict(rule),
            "trades": n, "known": n_known, "coverage": _num(n_known / n if n else 0.0, 4),
            "history_days": days, "kept": n_kept, "removed": n - n_kept, "kept_known": int(passes.sum()),
            "kept_mean_r": _num(kept_mean), "kept_t_stat": _num(t_stat(kept_r)), "kept_total_r": _num(kept_total),
            "all_mean_r": _num(all_mean), "random_mean_total_r": _num(float(rand.mean())),
            "random_p95_total_r": _num(float(np.percentile(rand, 95))), "percentile": _num(pct, 2),
            "p_value": _num(p_value), "verdict": verdict,
        })
    # Family-wise control: with k rules tested at 95% each, a noise feature would slip through ~1-(0.95^k) of the
    # time (k=7: ~30%). A rule is admitted only if it also clears Bonferroni (p <= 0.05 / k); one that clears only
    # the per-rule bar is "promising": reported, never proposed.
    tested = [p for p in per_rule if p["verdict"] != "insufficient"]
    alpha = 0.05 / max(len(tested), 1)
    for p in per_rule:
        p["bonferroni_pass"] = bool(p["verdict"] == "admit" and p["p_value"] <= alpha)
        if p["verdict"] == "admit" and not p["bonferroni_pass"]:
            p["verdict"] = "promising"
    sessions = sorted(t["session"].unique()) if n else []
    return {
        "kind": "ablation", "story": "S39", "source": source, "git_sha": git_sha,
        "config_sha256": config.sha256, "seed": config.seed, "n_random": config.n_random,
        "thresholds": {"admit_percentile": ADMIT_PERCENTILE, "admit_needs_kept_mean_above_all": True,
                       "admit_needs_bonferroni": True, "family_alpha": 0.05,
                       "min_history_days": MIN_HISTORY_DAYS, "min_known_trades": MIN_KNOWN_TRADES,
                       "unknown_values": "kept"},
        "trades": n, "trading_days": len(sessions),
        "window": [str(sessions[0]), str(sessions[-1])] if sessions else [None, None],
        "lookups": len(feats), "lookup_errors": lookup_errors,
        "all_trades": {"mean_r": _num(all_mean), "t_stat": _num(t_stat(r)), "total_r": _num(float(r.sum()))},
        "rules": per_rule,
        "rules_tested": len(tested),
        "admitted": [p["name"] for p in per_rule if p["verdict"] == "admit"],
        "summary": {v: sum(p["verdict"] == v for p in per_rule)
                    for v in ("admit", "promising", "reject", "insufficient")},
        "note": "admit only PROPOSES a feature for rules_extra via a PR with a replay trial; it never enables trading",
    }
