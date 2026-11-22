"""The tier-one screen (M11 ticket 2.11, issue #462; docs/research/tier_one_screen/design.md; config/screen.yaml).
Pure: arrays in, numbers out; `read_ledger` reads one committed file. Nothing here trades or is imported by the
engine, and nothing here gives a go/no-go number: a survivor earns a pre-registration and an engine run, nothing else.

- `rank_ics`: per monthly decision day, the Spearman correlation across stocks between a candidate's score and
  the next 21 sessions' return minus SPY's, where a stock entering the top quintile pays the round-trip charge;
- `statistic`: the mean of those, its Newey-West t and one-sided p (`gate_v3.mean_stats`);
- `screen_batch`: Benjamini-Hochberg at the batch's level q_b over the batch's p-values;
- the budget: alpha-investing over batches (W_0 = 0.09; q_b = half the wealth left, at most 0.10; a batch costs
  q_b / (1 - q_b); 0.10 back per survivor the engine confirms with a gate v3 `pass`; no batch below 0.005).

Screen candidates are logged as trials (n_trials 1, `variant: screen`) under `screen:<batch id>:<candidate>`.
They do NOT count in S4: S4 counts the configurations of pre-registered rules (Product, 22 Nov 2026; design section 6).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from kaibot.evaluation.gate_v3 import benjamini_hochberg, mean_stats

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config" / "screen.yaml"


def load_config(path: Path | str = CONFIG) -> dict:
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    b = cfg.get("budget") or {}
    for k in ("mfdr", "initial_wealth", "spend_fraction", "max_level", "payout", "floor"):
        if not isinstance(b.get(k), int | float) or isinstance(b.get(k), bool) or not 0 < float(b[k]) < 1:
            raise ValueError(f"screen.yaml: budget.{k} must be a number between 0 and 1")
    if b["initial_wealth"] > b["mfdr"] * (1 - b["mfdr"]) + 1e-12:
        raise ValueError("screen.yaml: initial_wealth above mfdr x (1 - mfdr) does not hold the mFDR")
    if b["payout"] > b["mfdr"] + 1e-12:
        raise ValueError("screen.yaml: a payout above the mFDR level does not hold it")
    return cfg


# ---------------------------------------------------------------- the statistic


def _ranks(x: np.ndarray) -> np.ndarray:
    return pd.Series(x).rank(method="average").to_numpy()


def rank_ics(score: np.ndarray, fwd: np.ndarray, *, top_fraction: float = 0.20, charge: float = 0.002,
             min_stocks: int = 30) -> np.ndarray:
    """One Spearman correlation per decision row. `score`, `fwd`: (decisions x stocks); NaN = not ranked that day.
    A stock in the top `top_fraction` by score that was not in it at the previous decision pays `charge` (the round
    trip, as a fraction) in its forward return. A row with fewer than `min_stocks` ranked stocks is NaN."""
    s, f = np.asarray(score, dtype=float), np.asarray(fwd, dtype=float)
    out = np.full(s.shape[0], np.nan)
    prev_top: set[int] = set()
    for t in range(s.shape[0]):
        ok = np.isfinite(s[t]) & np.isfinite(f[t])
        js = np.flatnonzero(ok)
        if js.size < min_stocks:
            prev_top = set()
            continue
        k = max(int(math.ceil(top_fraction * js.size)), 1)
        top = set(js[np.argsort(-s[t, js], kind="stable")[:k]].tolist())
        net = f[t, js].copy()
        for i, j in enumerate(js):
            if j in top and j not in prev_top:
                net[i] -= charge
        a, b = _ranks(s[t, js]), _ranks(net)
        if np.std(a) > 0 and np.std(b) > 0:
            out[t] = float(np.corrcoef(a, b)[0, 1])
        prev_top = top
    return out


def statistic(ics: np.ndarray, periods: int = 12) -> dict:
    """Mean rank IC, its Newey-West t and the one-sided p (the IC is above zero)."""
    x = np.asarray(ics, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 12:
        return {"months": int(x.size), "mean_ic": float(x.mean()) if x.size else math.nan, "t": math.nan,
                "p": 1.0, "why": "fewer than 12 months with a rank IC: p is set to 1"}
    m = mean_stats(x, periods)
    return {"months": int(x.size), "mean_ic": float(x.mean()), "t": float(m["t"]), "p": float(m["p_one_sided"]),
            "lags": m["lags"]}


# ---------------------------------------------------------------- the budget


@dataclass(frozen=True)
class Budget:
    initial_wealth: float = 0.09
    spend_fraction: float = 0.5
    max_level: float = 0.10
    payout: float = 0.10
    floor: float = 0.005

    @classmethod
    def from_config(cls, cfg: Mapping) -> Budget:
        b = cfg["budget"]
        return cls(float(b["initial_wealth"]), float(b["spend_fraction"]), float(b["max_level"]), float(b["payout"]),
                   float(b["floor"]))

    def level(self, wealth: float) -> float:
        """q_b for the next batch, or 0 when the wealth is below the floor (no batch may run)."""
        if wealth < self.floor - 1e-12:
            return 0.0
        return min(wealth * self.spend_fraction, self.max_level)

    @staticmethod
    def cost(level: float) -> float:
        return level / (1 - level) if level > 0 else 0.0


_ROW = re.compile(r"^\|\s*(?P<date>[^|]+?)\s*\|\s*(?P<batch>[^|]+?)\s*\|\s*(?P<kind>start|batch|payout)\s*\|\s*"
                  r"(?P<before>[^|]+?)\s*\|\s*(?P<level>[^|]+?)\s*\|\s*(?P<amount>[^|]+?)\s*\|\s*(?P<surv>[^|]+?)\s*\|"
                  r"\s*(?P<after>[^|]+?)\s*\|\s*$", re.M)


CONFIRMED = "pass"  # the only engine verdict that earns the payout (design section 4; evidence_based does not)


def read_ledger(path: Path | str, budget: Budget) -> dict:
    """Replay the committed ledger: the wealth left, the batches run, and every line checked. Raises ValueError
    when a line does not add up (the ledger is append-only and must be its own proof).

    A payout line refills the budget only for a survivor the engine confirmed (design section 4):
    `| date | <batch> | payout | before | pass | <payout> | <rule> | after |`, where <batch> is a batch already in
    the ledger, its level column is the gate v3 verdict (`pass` only), and its survivors column is the rule the
    survivor became. A batch pays at most once per survivor, and a rule only once."""
    rows = [m.groupdict() for m in _ROW.finditer(Path(path).read_text())]
    if not rows or rows[0]["kind"] != "start":
        raise ValueError(f"{path}: the ledger starts with a `start` line")
    wealth, batches = 0.0, []
    survivors: dict[str, int] = {}
    paid: dict[str, list[str]] = {}
    for i, r in enumerate(rows):
        before, after = float(r["before"]), float(r["after"])
        name = r["batch"].strip()
        if abs(before - wealth) > 1e-6:
            raise ValueError(f"{path}: {name} starts at {before}, the ledger stood at {wealth:.6f}")
        if r["kind"] == "start":
            if i != 0:
                raise ValueError(f"{path}: only the first line is a `start` line (Product resets by decision record)")
            amount = budget.initial_wealth
            want = before + amount
        elif r["kind"] == "batch":
            q = float(r["level"])
            if abs(q - budget.level(before)) > 1e-6:
                raise ValueError(f"{path}: batch {name} ran at {q}, the budget allowed "
                                 f"{budget.level(before):.6f}")
            if name in survivors:
                raise ValueError(f"{path}: batch {name} is in the ledger twice: a batch runs once")
            amount = budget.cost(q)
            want = before - amount
            survivors[name] = int(r["surv"])
            batches.append(name)
        else:
            rule, verdict = r["surv"].strip(), r["level"].strip()
            if name not in survivors:
                raise ValueError(f"{path}: a payout for {name}, which is no earlier batch of the ledger")
            if verdict != CONFIRMED:
                raise ValueError(f"{path}: a payout for {name} on the verdict {verdict!r}: only an engine "
                                 f"`{CONFIRMED}` refills the budget")
            if not rule or rule == "-" or any(rule in v for v in paid.values()):
                raise ValueError(f"{path}: a payout for {name} must name the confirmed rule, once")
            paid.setdefault(name, []).append(rule)
            if len(paid[name]) > survivors[name]:
                raise ValueError(f"{path}: batch {name} had {survivors[name]} survivors and pays "
                                 f"{len(paid[name])} times")
            amount = budget.payout
            want = before + amount
        if abs(float(r["amount"]) - amount) > 1e-6:
            raise ValueError(f"{path}: {name}'s amount is {r['amount']}, it should be {amount:.6f}")
        if abs(after - want) > 1e-6:
            raise ValueError(f"{path}: {name} ends at {after}, it should be {want:.6f}")
        wealth = after
    return {"wealth": wealth, "batches": batches, "lines": len(rows), "next_level": budget.level(wealth),
            "survivors": survivors, "paid": paid}


def payout_line(date: str, batch: str, rule: str, verdict: str, wealth: float, budget: Budget) -> str:
    """The ledger line for a survivor the engine confirmed; refused for any verdict but `pass`."""
    if verdict != CONFIRMED:
        raise ValueError(f"only an engine `{CONFIRMED}` refills the budget, not {verdict!r}")
    return f"| {date} | {batch} | payout | {wealth:.6f} | {verdict} | {budget.payout:.6f} | {rule} | " \
           f"{wealth + budget.payout:.6f} |"


def ledger_line(date: str, batch: str, wealth: float, level: float, survivors: int, budget: Budget) -> str:
    after = wealth - budget.cost(level)
    return f"| {date} | {batch} | batch | {wealth:.6f} | {level:.6f} | {budget.cost(level):.6f} | {survivors} | " \
           f"{after:.6f} |"


# ---------------------------------------------------------------- one batch


def screen_batch(pvalues: Mapping[str, float], level: float) -> dict:
    """Benjamini-Hochberg at the batch's level over its candidates' p-values. Level 0: nothing survives."""
    if level <= 0:
        return {k: {"p": float(v), "adjusted": 1.0, "survives": False} for k, v in pvalues.items()}
    bh = benjamini_hochberg(dict(pvalues), level)
    return {k: {"p": bh[k]["p"], "adjusted": bh[k]["adjusted"], "survives": bh[k]["rejected"]} for k in pvalues}


# ---------------------------------------------------------------- the candidate signals (point in time)

Signal = Callable[[np.ndarray, int], np.ndarray]


def _ret(c: np.ndarray, k: int, back: int, skip: int = 0) -> np.ndarray:
    a, b = k - skip, k - back
    if b < 0:
        return np.full(c.shape[1], np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return c[a] / c[b] - 1


def _slope_r2(c: np.ndarray, k: int, window: int) -> np.ndarray:
    if k - window + 1 < 0:
        return np.full(c.shape[1], np.nan)
    from kaibot.strategies.momentum import regression_score

    return regression_score(c[k - window + 1:k + 1])


SIGNALS: dict[str, Callable[..., Signal]] = {
    "momentum": lambda back=252, skip=21: (lambda c, k: _ret(c, k, back, skip)),
    "reversal": lambda back=5: (lambda c, k: -_ret(c, k, back)),
    "trend_slope": lambda window=90: (lambda c, k: _slope_r2(c, k, window)),
}


def candidate_scores(c: np.ndarray, ok: np.ndarray, rows: np.ndarray, signal: str, params: Mapping) -> np.ndarray:
    """(decisions x stocks) scores read from closes at or before each decision row only; NaN outside `ok`."""
    if signal not in SIGNALS:
        raise ValueError(f"unknown signal {signal!r}; known: {sorted(SIGNALS)}")
    fn = SIGNALS[signal](**dict(params))
    out = np.vstack([np.where(ok[k], fn(c, int(k)), np.nan) for k in rows])
    return out


def forward_excess(c: np.ndarray, spy: np.ndarray, rows: np.ndarray, horizon: int) -> np.ndarray:
    """The next `horizon` sessions' return of each stock minus SPY's, from decision row k (NaN past the data)."""
    n = c.shape[0]
    out = np.full((len(rows), c.shape[1]), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        for i, k in enumerate(rows):
            e = int(k) + horizon
            if e < n:
                out[i] = (c[e] / c[k] - 1) - (spy[e] / spy[k] - 1)
    return out
