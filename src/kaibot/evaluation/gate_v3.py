"""Evidence gate v3 for multi-day rules (decision 16; M11 ticket 2.5): the sleeve test, the book test, the
correction across rule families and the final check on the hold-out. Reports only: nothing here is imported by
the engine and nothing here can trade.

Why a third gate. Gate v2 judged each rule alone in a cash account against a fully invested SPY, so a rule that
is 36% invested needed about 34% a year on its invested money to pass, whatever its skill. And it asked each
rule separately to beat 90% of random portfolios, so with a dozen ideas tried the chance that one passes with no
skill was about 70%. Gate v3 changes what is measured:

- a rule is a SLEEVE: its return is compared with the same amount of SPY (`active` below), so being part-time
  invested is neither punished nor rewarded;
- the account that would trade is a BOOK: gate v2's absolute test (return, drawdown against SPY) is kept for it;
- chance is judged once for the whole programme: Holm's correction across rule FAMILIES, and a deflated Sharpe
  that counts the family's own configurations.

The pass marks are pre-declared in `config/gate_v3.yaml`; every report carries its sha256 as `gate_sha`.

Fail closed, as gate v2: a mark that cannot be computed is `None` and `None` never passes; a run from the
research harness, cost model v1, today's stock list or price-only SPY is `provisional`; so is a rule whose
pre-registration does not declare its gate v3 role, kind, family and candidate pool (every rule registered
before decision 16), which is why a re-score of an existing report can fail but can never pass.

Amendment 1 (decision 16 section 15, ticket 2.12): S4's trial-Sharpe variance has a floor of 1/(T-1); a family
of two or more rules needs 2,000 control runs; the selection benchmark's first session holds the exposure of the
close before the window; the evidence-based tier of section 14 (`tier: evidence_based` in a rule's entry: S1, S2,
S5 and S6 judged, S3 and S4 printed, verdict `evidence_based`, labelled, at most 10% of a book together); and a
book is listed under `books:` (not in a family) with its components and sleeve budgets.

Every series is cut to the research window first. Sessions in the hold-out are dropped before any number is
computed and are counted in the report (`sessions_in_holdout_dropped`).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from kaibot.evaluation import mtm
from kaibot.evaluation.gate_v2 import _clean, _iso, _mark, _pct, _state, _utc
from kaibot.evaluation.prereg import Prereg
from kaibot.evaluation.random_control import p_from_percentile
from kaibot.evaluation.trials import Registry, count_configurations

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = {
    "window": ("research_from", "research_to"),
    "gate_a": ("max_neighbours", "must_declare"),
    "sleeve": ("benchmark", "s1_active_return", "s2_costs", "s3_control", "s4_deflated_sharpe", "s5_universe",
               "s6_neighbours", "final_requires"),
    "book": ("b1_return", "b2_spy", "b3_costs", "b4_universe", "b5_neighbours", "b6_sleeves", "final_requires"),
    "holdout_check": ("band", "contaminated_before"),
    "bootstrap": ("method", "mean_block_sessions", "runs", "seed", "interval"),
    "statistics": ("periods_per_year", "sharpe_haircut", "sharpe_warning_above"),
}
KINDS = ("selection", "timing")
TIERS = ("full", "evidence_based")
EVIDENCE_LABEL = "evidence-based, not proven here"
SCORABLE_CONTROLS = ("candidate_pool", "random_timing")


# ---------------------------------------------------------------- pre-registered marks


@dataclass(frozen=True)
class GateV3:
    cfg: dict
    sha: str

    @classmethod
    def load(cls, root: Path | str | None = None) -> GateV3:
        return cls.from_bytes((Path(root or ROOT) / "config" / "gate_v3.yaml").read_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes) -> GateV3:
        cfg = yaml.safe_load(raw) or {}
        for section, keys in REQUIRED.items():
            missing = [k for k in keys if k not in cfg.get(section, {})]
            if missing:
                raise ValueError(f"config/gate_v3.yaml: {section} lacks {', '.join(missing)}")
        if not 0.0 < float(cfg.get("level", 0)) < 1.0 or not cfg.get("families"):
            raise ValueError("config/gate_v3.yaml: needs a `level` between 0 and 1 and the `families`")
        seen: dict[str, str] = {}
        for fam, body in cfg["families"].items():
            for rule, r in (body.get("rules") or {}).items():
                if rule in seen:
                    raise ValueError(f"config/gate_v3.yaml: {rule} is in two families ({seen[rule]}, {fam})")
                if r.get("kind") not in KINDS:
                    raise ValueError(f"config/gate_v3.yaml: {rule}: kind must be one of {KINDS}")
                seen[rule] = fam
            if not body.get("rules"):
                raise ValueError(f"config/gate_v3.yaml: family {fam} has no rule")
            for rule, r in (body.get("rules") or {}).items():
                tier = r.get("tier", "full")
                if tier not in TIERS:
                    raise ValueError(f"config/gate_v3.yaml: {rule}: tier must be one of {TIERS}")
        for name, b in (cfg.get("books") or {}).items():
            if name in seen:
                raise ValueError(f"config/gate_v3.yaml: {name} is both a book and a rule")
            if not isinstance(b, dict) or not isinstance(b.get("components"), list) \
                    or not isinstance(b.get("sleeves"), dict) or not isinstance(b.get("v3_declared"), bool):
                raise ValueError(f"config/gate_v3.yaml: books.{name} needs `components` (a list), `sleeves` (rule: "
                                 "budget %) and `v3_declared`")
            for rule, pct in b["sleeves"].items():
                if isinstance(pct, bool) or not isinstance(pct, int | float) or not 0 < float(pct) <= 100:
                    raise ValueError(f"config/gate_v3.yaml: books.{name}.sleeves.{rule}: the budget must be a "
                                     "number in (0, 100] (% of the book)")
        if cfg["bootstrap"]["method"] != "stationary":
            raise ValueError("config/gate_v3.yaml: only the stationary bootstrap is implemented")
        return cls(cfg, hashlib.sha256(raw).hexdigest())

    @property
    def level(self) -> float:
        return float(self.cfg["level"])

    @property
    def window(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        w = self.cfg["window"]
        return pd.Timestamp(str(w["research_from"])), pd.Timestamp(str(w["research_to"]))

    @property
    def stats_kw(self) -> dict:
        s = self.cfg["statistics"]
        return {"periods": int(s["periods_per_year"]), "haircut": float(s["sharpe_haircut"]),
                "warn_above": float(s["sharpe_warning_above"])}

    @property
    def families(self) -> dict[str, dict]:
        return self.cfg["families"]

    def family_of(self, rule: str) -> str | None:
        return next((f for f, body in self.families.items() if rule in body["rules"]), None)

    def rule(self, rule: str) -> dict | None:
        fam = self.family_of(rule)
        return None if fam is None else dict(self.families[fam]["rules"][rule])

    def book(self, name: str) -> dict | None:
        b = (self.cfg.get("books") or {}).get(name)
        return None if b is None else dict(b)

    def tier(self, rule: str) -> str:
        return (self.rule(rule) or {}).get("tier", "full")

    @property
    def evidence(self) -> dict:
        return dict(self.cfg.get("evidence_based") or {})

    def cut(self, s: pd.Series, name: str = "returns") -> tuple[pd.Series, int, int]:
        """The series on the research window, and how many sessions were dropped before and after it."""
        s = mtm.dated(s, name)
        lo, hi = self.window
        keep = s[(s.index >= lo) & (s.index <= hi)]
        return keep, int((s.index < lo).sum()), int((s.index > hi).sum())


@dataclass(frozen=True)
class Account:
    """One simulated account as gate v3 reads it (a sleeve, a neighbour or a book)."""

    net: pd.Series  # daily mark-to-market returns after costs, idle cash at 0%, indexed by session
    spy: pd.Series  # SPY total return on (at least) the same sessions
    exposure: pd.Series | None = None  # share of equity invested at each close (a sleeve needs it)
    costs: pd.Series | None = None  # each session's costs at 1x, as a share of that session's starting equity
    control: Any = None  # the matched random control: a `ControlResult`, its dict, or a report's control block
    universe: str = "today"  # "point_in_time" | "today"


# ---------------------------------------------------------------- the correction across families


def holm(pvalues: Mapping[str, float | None], level: float) -> dict[str, dict]:
    """Holm's (1979) step-down correction. `pvalues`: one per hypothesis; None = no usable p-value, counted as 1
    (it is in the family count and is never rejected).

    Sort the m p-values upward. The i-th smallest (i = 1..m) is rejected when it and every smaller one satisfy
    p_(j) <= level / (m - j + 1). The adjusted p-value is max over j <= i of (m - j + 1) * p_(j), capped at 1;
    "rejected" is the same as adjusted <= level. The probability of rejecting ANY true null is at most `level`,
    whatever the dependence between the tests. With no other p-value known this is Bonferroni: p <= level / m."""
    p = {k: 1.0 if v is None or not math.isfinite(float(v)) else min(max(float(v), 0.0), 1.0)
         for k, v in pvalues.items()}
    m = len(p)
    out, running = {}, 0.0
    for i, k in enumerate(sorted(p, key=lambda k: (p[k], k)), start=1):
        running = max(running, min(1.0, (m - i + 1) * p[k]))
        out[k] = {"p": p[k], "known": pvalues[k] is not None, "rank": i, "threshold": level / (m - i + 1),
                  "adjusted": running, "rejected": bool(running <= level)}
    return out


def benjamini_hochberg(pvalues: Mapping[str, float | None], level: float) -> dict[str, dict]:
    """Benjamini-Hochberg (1995) step-up, reported beside Holm and not judged: it bounds the expected SHARE of
    false passes among the passes (not the chance of any), and needs independent or positively dependent tests.
    Adjusted p-value of the i-th smallest: min over j >= i of m * p_(j) / j, capped at 1."""
    p = {k: 1.0 if v is None or not math.isfinite(float(v)) else min(max(float(v), 0.0), 1.0)
         for k, v in pvalues.items()}
    m = len(p)
    order = sorted(p, key=lambda k: (p[k], k))
    out, running = {}, 1.0
    for i in range(m, 0, -1):
        k = order[i - 1]
        running = min(running, m * p[k] / i)
        out[k] = {"p": p[k], "rank": i, "adjusted": running, "rejected": bool(running <= level)}
    return out


def control_numbers(control: Any) -> dict | None:
    """The control as plain numbers, from a `ControlResult`, its dict, or the block a report stores (which
    nests the result under `control_result` and names the percentile `sharpe_percentile`)."""
    if control is None:
        return None
    c = control.to_dict() if hasattr(control, "to_dict") else dict(control)
    inner = c.get("control_result") if isinstance(c.get("control_result"), Mapping) else {}
    pct = c.get("sharpe_percentile", c.get("percentile", inner.get("percentile")))
    if c.get("percentile_simulated", inner.get("percentile_simulated")) is not None and pct is not None \
            and "sharpe_percentile" not in c:
        pct = min(float(pct), float(c.get("percentile_simulated", inner.get("percentile_simulated"))))
    runs = int(c.get("runs") or inner.get("runs") or 0)
    p = c.get("p_value", inner.get("p_value"))
    if pct is not None and runs and (p is None or "sharpe_percentile" in c):
        p = p_from_percentile(float(pct), runs)  # the percentile used (the lower of the two) decides
    return {"percentile": None if pct is None else float(pct), "runs": runs, "matched": bool(c.get("matched")),
            "p_value": None if p is None else float(p), "kind": c.get("kind"),
            "pool": c.get("pool", inner.get("pool")),
            "drawdown_percentile": c.get("drawdown_percentile", inner.get("drawdown_percentile"))}


def family_p(gate: GateV3, rule_p: Mapping[str, float | None]) -> dict[str, float | None]:
    """One p-value per family from the p-values of its rules: the smallest, times the number of rules the family
    lists (Bonferroni inside the family, whether or not every member has a p-value). None when no member has
    one."""
    out: dict[str, float | None] = {}
    for fam, body in gate.families.items():
        known = [float(rule_p[r]) for r in body["rules"] if rule_p.get(r) is not None]
        out[fam] = min(1.0, len(body["rules"]) * min(known)) if known else None
    return out


# ---------------------------------------------------------------- active return


def benchmark(gate: GateV3, kind: str, spy: pd.Series, exposure: pd.Series, rf: pd.Series | None,
              prior: float = 0.0) -> pd.Series:
    """The sleeve's benchmark: the same amount of SPY, the rest in Treasury bills (`rf`; None: at 0%).

    selection: yesterday's close exposure times today's SPY return (`mtm.exposure_matched`): the sleeve's own
    timetable of being invested is copied, so only the choice of names is judged.
    timing: the sleeve's AVERAGE exposure times SPY, every session: for an SPY-only rule the timetable is the
    skill, so it must not be copied.

    `prior` (amendment 1): the exposure at the close before the first session (0 when the account starts on it),
    so a sleeve invested going into the window is compared with SPY held at that exposure on its first session."""
    how = gate.cfg["sleeve"]["benchmark"][kind]
    e = exposure.to_numpy(dtype=float)
    held = np.r_[float(prior), e[:-1]] if how == "path_matched" else np.full(e.size, float(e.mean()))
    out = held * spy.to_numpy(dtype=float)
    if rf is not None:
        out = out + np.clip(1.0 - held, 0.0, 1.0) * rf.to_numpy(dtype=float)
    return pd.Series(out, index=spy.index)


def active_return(gate: GateV3, kind: str, acc: Account, tbill: pd.Series | None = None) -> dict:
    """The sleeve minus its benchmark on the research window, both with idle cash in Treasury bills when the
    rate is given. For a selection sleeve the bills cancel exactly (both sides hold the same cash), so the
    active return does not depend on the rate; for a timing sleeve they do not, and the rate matters."""
    if acc.exposure is None:
        raise ValueError("a sleeve needs its exposure series: the benchmark holds the same amount of SPY")
    net, before, after = gate.cut(acc.net, "sleeve returns")
    if net.size < 30:
        raise ValueError(f"{net.size} sessions on the research window: nothing to judge")
    net, spy = mtm.align(net, acc.spy)
    full = mtm.dated(acc.exposure, "exposure")
    e = mtm.align(net, full)[1]
    before_first = full[full.index < net.index[0]]
    prior = float(before_first.iloc[-1]) if len(before_first) and math.isfinite(before_first.iloc[-1]) else 0.0
    held = np.r_[prior, e.to_numpy(dtype=float)[:-1]]  # the exposure going into each session (amendment 1)
    rf = mtm.tbill_daily(tbill, net.index) if tbill is not None else None
    mine = net if rf is None else pd.Series(net.to_numpy() + np.clip(1.0 - held, 0.0, 1.0) * rf.to_numpy(),
                                            index=net.index)
    bench = benchmark(gate, kind, spy, e, rf, prior=prior)
    costs = mtm.align(net, mtm.dated(acc.costs, "costs"))[1] if acc.costs is not None else None
    return {"active": mine - bench, "net": net, "with_cash": mine, "benchmark": bench, "spy": spy, "exposure": e,
            "held": held,
            "rf": rf, "costs": costs, "dropped_before": before, "dropped_holdout": after}


def mean_stats(x, periods: int = mtm.PERIODS) -> dict:
    """Annualised mean of a daily series, its Newey-West standard error, t and one-sided p (normal)."""
    a = np.asarray(x, dtype=float)
    n = a.size
    mu = float(a.mean())
    lag = mtm.nw_lag(n)
    var = float(mtm.hac_long_run((a - mu)[:, None], lag)[0, 0]) / n
    se = math.sqrt(var) if var > 0 else math.inf
    t = mu / se if math.isfinite(se) and se > 0 else 0.0
    sd = float(a.std(ddof=1))
    return {"sessions": n, "annualised": mu * periods, "se": se * periods, "t": t, "lags": lag,
            "p_one_sided": float(1.0 - mtm.norm_cdf(t)), "tracking_error": sd * math.sqrt(periods),
            "information_ratio": mu / sd * math.sqrt(periods) if sd > 0 else 0.0}


def _boot_kw(gate: GateV3) -> dict:
    b = gate.cfg["bootstrap"]
    return {"runs": int(b["runs"]), "mean_block": float(b["mean_block_sessions"]), "seed": int(b["seed"])}


def _interval(gate: GateV3, values: np.ndarray) -> list[float]:
    tail = (1.0 - float(gate.cfg["bootstrap"]["interval"])) / 2.0
    return [float(np.quantile(values, tail)), float(np.quantile(values, 1.0 - tail))]


def bootstrap_paths(gate: GateV3, n: int, horizon: int | None = None) -> np.ndarray:
    """Row indices of the gate's stationary-bootstrap resamples (seeded: the same inputs give the same rows).
    Two series resampled with the same rows stay paired, session for session."""
    kw = _boot_kw(gate)
    return mtm.stationary_indices(n, horizon or n, kw["runs"], kw["mean_block"], np.random.default_rng(kw["seed"]))


def _path_cagr(paths: np.ndarray, periods: int) -> np.ndarray:
    growth = np.prod(1.0 + paths, axis=1)
    return np.where(growth > 0, growth, np.nan) ** (periods / paths.shape[1]) - 1.0


# ---------------------------------------------------------------- Gate A and the source


def _gate_a(gate: GateV3, rule: str, prereg: Prereg | None, registry: Registry | None,
            first_run_at: datetime | None, source: Mapping | None, need: Mapping,
            role: str = "sleeve") -> tuple[dict, list[str]]:
    src = dict(source or {})
    reasons = [f"{k} is {src.get(k, 'not stated')!r}, a pass needs {v!r}" for k, v in need.items() if src.get(k) != v]
    entry = gate.rule(rule) if role == "sleeve" else gate.book(rule)
    first = None if prereg is None else (prereg.first_committed_at or prereg.committed_at)
    before = None if first is None or first_run_at is None else _utc(first) <= _utc(first_run_at)
    logged = {r.get("prereg_sha256") for r in (registry.rows if registry is not None else ())
              if r.get("strategy") == rule and r.get("prereg_sha256")}
    unchanged = None if prereg is None or not logged else all(prereg.accepts(h) for h in logged)
    checks = {"preregistered": prereg is not None,
              "not_retroactive": None if prereg is None else not prereg.retroactive,
              "neighbours_at_most": None if prereg is None else
              len(prereg.neighbours) <= gate.cfg["gate_a"]["max_neighbours"],
              "committed_before_first_run": before, "unchanged_since_logged_runs": unchanged,
              "declares_gate_v3": bool(entry and entry.get("v3_declared"))}
    if role == "sleeve":
        checks.update(in_a_family=entry is not None, family=gate.family_of(rule), tier=gate.tier(rule))
    else:  # amendment 1: a book is listed under `books:`; it is in no family and not in the correction's count
        checks.update(listed_as_book=entry is not None,
                      components=list((entry or {}).get("components") or []),
                      sleeve_budgets=dict((entry or {}).get("sleeves") or {}))
    if prereg is None:
        reasons.append("no pre-registration was supplied (Gate A)")
    else:
        if prereg.retroactive:
            reasons.append("the pre-registration is retroactive: written after the first run (Gate A)")
        if not checks["neighbours_at_most"]:
            reasons.append(f"{len(prereg.neighbours)} neighbours declared, at most "
                           f"{gate.cfg['gate_a']['max_neighbours']} allowed (Gate A)")
        if before is not True:
            reasons.append("the pre-registration is not shown to be committed before the first run (Gate A)")
        if unchanged is False:
            reasons.append("a run of this rule was logged with a different pre-registration hash (Gate A)")
    if entry is None and role == "book":
        reasons.append("the book is not listed under `books:` of config/gate_v3.yaml: its pre-registration adds "
                       "it, with its components and sleeve budgets, before its first run")
    elif entry is None:
        reasons.append("the rule is in no family of config/gate_v3.yaml: it must be added before its first run")
    elif role == "sleeve" and entry.get("tier") == "evidence_based" and not str(entry.get("evidence") or "").strip():
        reasons.append("the rule's entry takes the evidence-based tier but cites no evidence (decision 16 "
                       "section 14: strong published, replicated evidence, cited in the pre-registration)")
    if role == "sleeve" and entry is not None and entry.get("tier") == "evidence_based" and prereg is not None:
        # QA #542: section 14 puts the citation and the label in the pre-registration itself, not only in the config
        secs = dict(prereg.sections or {})
        label = str(gate.evidence.get("label", EVIDENCE_LABEL))
        if not str(secs.get("evidence") or "").strip():
            reasons.append("the rule takes the evidence-based tier but its pre-registration's published-evidence "
                           "section is empty (decision 16 section 14)")
        if label.lower() not in " ".join(str(v) for v in secs.values()).lower():
            reasons.append(f"the rule takes the evidence-based tier but its pre-registration does not carry the "
                           f"label \"{label}\" (decision 16 section 14)")
        checks.update(prereg_cites_evidence=bool(str(secs.get("evidence") or "").strip()),
                      prereg_carries_label=label.lower() in " ".join(str(v) for v in secs.values()).lower())
    if entry is not None and not entry.get("v3_declared"):
        reasons.append("the pre-registration does not declare its gate v3 role, kind, family and candidate pool "
                       "(it predates decision 16): this is a re-score, not a verdict")
    return checks, reasons


def _verdict(marks: Mapping[str, dict], reasons: Sequence[str]) -> str:
    met = [m["met"] for m in marks.values()]
    if any(v is False for v in met):
        return "fail"
    if any(v is None for v in met):
        return "incomplete"
    return "provisional" if reasons else "pass"


def _all(states: Sequence[bool | None]) -> bool | None:
    """All hold -> True; any is false -> False; otherwise unknown."""
    if any(v is False for v in states):
        return False
    return None if any(v is None for v in states) else True


# ---------------------------------------------------------------- the sleeve marks


def s1(gate: GateV3, active: pd.Series) -> dict:
    """Annualised mean active return above zero. Its bootstrap interval is printed, not judged."""
    lim = float(gate.cfg["sleeve"]["s1_active_return"]["annualised_above"])
    st = mean_stats(active, gate.stats_kw["periods"])
    a = active.to_numpy()
    boot = a[bootstrap_paths(gate, a.size)].mean(axis=1) * gate.stats_kw["periods"]
    met = bool(st["annualised"] > lim)
    return _mark(met, {**st, "interval": _interval(gate, boot),
                       "interval_level": float(gate.cfg["bootstrap"]["interval"]),
                       "bootstrap_share_above_zero": float((boot > 0).mean())},
                 {"annualised_above": lim}, "" if met else "the active return is not above zero")


def s2(gate: GateV3, active: pd.Series, costs: pd.Series | None) -> dict:
    """S1 still holds when every cost is charged a second time."""
    c = gate.cfg["sleeve"]["s2_costs"]
    k, lim = float(c["costs_multiple"]), float(gate.cfg["sleeve"]["s1_active_return"]["annualised_above"])
    limit = {"costs_multiple": k, "annualised_above": lim}
    if costs is None:
        return _mark(None, {}, limit, "no daily cost series was supplied")
    per = gate.stats_kw["periods"]
    at = {f"{m:g}x": float((active - (m - 1.0) * costs).mean() * per) for m in c["reported_multiples"]}
    cost_ann = float(costs.mean() * per)
    gross = float((active + costs).mean() * per)
    met = bool(float((active - (k - 1.0) * costs).mean() * per) > lim)
    return _mark(met, {"active_return_at": at, "costs_a_year": cost_ann,
                       "breakeven_multiple": gross / cost_ann if cost_ann > 0 else math.inf},
                 limit, "" if met else f"the active return is not above zero at {k:g}x costs")


def s3(gate: GateV3, rule: str, control: Any, other_family_p: Mapping[str, float | None] | None = None,
       control_source: str | None = None) -> dict:
    """Skill against chance: the control's p-value, Bonferroni inside the family, Holm across the families.

    control_source: what the control drew from, when the control itself does not say (reports written before
    decision 16): one of the `existing_control` values of config/gate_v3.yaml."""
    c = gate.cfg["sleeve"]["s3_control"]
    fam = gate.family_of(rule)
    several = fam is not None and len(gate.families[fam]["rules"]) > 1
    # amendment 1: a family of several rules multiplies its smallest p-value by its size, so 500 runs (p >= 1/501)
    # can hardly clear the correction; such a family needs 2,000 or more
    runs_min = int(c.get("random_runs_min_several_rules", c["random_runs_min"])) if several \
        else int(c["random_runs_min"])
    limit = {"runs_min": runs_min, "pool": c["pool"], "family_wise_level": gate.level,
             "families": len(gate.families)}
    n = control_numbers(control)
    if fam is None:
        return _mark(None, n or {}, limit, "the rule is in no family of config/gate_v3.yaml")
    if n is None:
        return _mark(None, {"family": fam}, limit, "no matched random control was supplied")
    n["family"] = fam
    kind = gate.rule(rule)["kind"]
    drew = n["pool"] if n["pool"] not in (None, "universe") or control_source is None else control_source
    if kind == "timing" and (n.get("kind") or "").startswith("random_timing"):
        drew = "random_timing"
    n["drew_from"] = drew
    if n["runs"] < limit["runs_min"]:
        return _mark(None, n, limit, f"{n['runs']} runs, {limit['runs_min']} needed")
    if not n["matched"]:
        return _mark(None, n, limit, "the control did not fill every slot, so it is not matched")
    if n["p_value"] is None:
        return _mark(None, n, limit, "the control has no percentile")
    if drew not in SCORABLE_CONTROLS:
        return _mark(None, n, limit, f"the control drew from {drew or 'an unstated pool'}, not from the rule's own "
                                     "candidate pool")
    members = len(gate.families[fam]["rules"])
    mine = min(1.0, members * n["p_value"])
    others = {f: (other_family_p or {}).get(f) for f in gate.families if f != fam}
    allp = {**others, fam: mine}
    h, bh = holm(allp, gate.level)[fam], benjamini_hochberg(allp, gate.level)[fam]
    n.update(family_members=members, family_p=mine, holm_adjusted=h["adjusted"], holm_rank=h["rank"],
             holm_threshold=h["threshold"], families_with_a_p_value=sum(v is not None for v in allp.values()),
             bh_adjusted=bh["adjusted"], bh_would_pass=bh["rejected"])
    met = bool(h["rejected"])
    return _mark(met, n, limit, "" if met else
                 f"family p {mine:.4f} is above {h['threshold']:.4f} (Holm, {len(allp)} families, level "
                 f"{gate.level:g})")


def s4(gate: GateV3, rule: str, active: pd.Series, registry: Registry | None) -> dict:
    """Deflated Sharpe of the active return against the family's own configurations."""
    lim = float(gate.cfg["sleeve"]["s4_deflated_sharpe"]["dsr_min"])
    limit = {"dsr_min": lim, "trials": "family"}
    fam = gate.family_of(rule)
    if registry is None or fam is None:
        return _mark(None, {}, limit, "no trial registry was supplied" if fam else "the rule is in no family")
    members = set(gate.families[fam]["rules"])
    rows = [r for r in registry.rows if r.get("strategy") in members]
    n = count_configurations(rows)
    sharpes = [s for m in members for s in registry.daily_sharpes().get(m, [])]
    var = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else None
    if n < 1:
        return _mark(None, {"family": fam, "configurations": 0}, limit,
                     "the registry holds no trial of this family: the run itself must be logged first")
    t = int(np.asarray(active).size)
    floor = None
    if gate.cfg["sleeve"]["s4_deflated_sharpe"].get("variance_floor") == "one_over_t_minus_1" and t > 1:
        # amendment 1: near-identical configurations have almost no spread of Sharpes, which makes the hurdle
        # vanish; the spread used is never below the sampling variance of an unskilled Sharpe over T sessions
        floor = 1.0 / (t - 1)
    used = var if floor is None else max(var or 0.0, floor)
    d = mtm.deflated_sharpe(active, n, used, periods=gate.stats_kw["periods"])
    met = bool(d.dsr >= lim)
    if floor is not None:
        limit["variance_floor"] = "1/(T-1)"
    return _mark(met, {**asdict(d), "family": fam, "configurations": n, "trial_sharpes_used": len(sharpes) if var
                       else 0, "whole_registry_configurations": registry.configurations, "sessions_judged": t,
                       "variance_of_trial_sharpes": var, "variance_floor": floor,
                       "variance_floor_binds": bool(floor is not None and (var is None or var < floor))},
                 limit, "" if met else f"deflated Sharpe {d.dsr:.3f} is below {lim:g}")


def _marks_s1_to_s4(gate: GateV3, rule: str, kind: str, acc: Account, tbill, registry, other_family_p,
                    control_source) -> tuple[dict, dict]:
    a = active_return(gate, kind, acc, tbill)
    return {"s1": s1(gate, a["active"]), "s2": s2(gate, a["active"], a["costs"]),
            "s3": s3(gate, rule, acc.control, other_family_p, control_source),
            "s4": s4(gate, rule, a["active"], registry)}, a


def evaluate_sleeve(gate: GateV3, *, rule: str, account: Account, pit_account: Account | None = None,
                    neighbours: Mapping[str, Account] | None = None, prereg: Prereg | None = None,
                    source: Mapping[str, Any] | None = None, registry: Registry | None = None,
                    first_run_at: datetime | None = None, tbill: pd.Series | None = None,
                    other_family_p: Mapping[str, float | None] | None = None, control_source: str | None = None,
                    kind: str | None = None, as_of: datetime | None = None) -> dict:
    """The sleeve test for one rule: marks S1 to S6 with pass / fail / unknown and their numbers.

    account: the sleeve's run. pit_account: the same rule on the point-in-time universe when `account` is on
    today's list. neighbours: each pre-declared neighbour's account (its returns and exposure are enough).
    tbill: the Treasury-bill rate (FRED DTB3, percent). other_family_p: the p-value of every OTHER family (None
    or missing: counted as 1, which is the strict reading). control_source: see `s3`. kind: "selection" or
    "timing" (default: the rule's entry in config/gate_v3.yaml; a rule in no family is scored as a selection
    sleeve and cannot pass).

    verdict: "fail", "incomplete", "provisional" (all marks hold but the run or the pre-registration cannot carry
    a pass) or "pass"."""
    entry = gate.rule(rule)
    kind = kind or (entry or {}).get("kind") or "selection"
    control_source = control_source or (entry or {}).get("existing_control")
    marks, a = _marks_s1_to_s4(gate, rule, kind, account, tbill, registry, other_family_p, control_source)
    tier = gate.tier(rule)
    required = list(gate.evidence.get("marks_required") or ("s1", "s2", "s5", "s6")) \
        if tier == "evidence_based" else ["s1", "s2", "s3", "s4", "s5", "s6"]
    want = gate.cfg["sleeve"]["s5_universe"]
    if account.universe == want:
        got, where = {k: v["met"] for k, v in marks.items()}, "this run is on the point-in-time universe"
    elif pit_account is not None and pit_account.universe == want:
        got = {k: v["met"] for k, v in _marks_s1_to_s4(gate, rule, kind, pit_account, tbill, registry,
                                                        other_family_p, control_source)[0].items()}
        where = "a second run on the point-in-time universe"
    else:
        got, where = None, ""
    # the evidence-based tier asks S5 of the marks it judges (S1, S2), not of the printed S3 and S4
    which = "S1 to S4" if tier != "evidence_based" else ", ".join(k.upper() for k in required if k in
                                                                    ("s1", "s2", "s3", "s4"))
    held = None if got is None else _all([v for k, v in got.items() if k in required])
    marks["s5"] = _mark(None, {"universe": account.universe}, {"universe": want},
                        "no run on the point-in-time universe was supplied") if got is None else \
        _mark(held, {"universe": want, "source": where, **got}, {"universe": want},
              {True: "", False: f"{which} do not all hold on the point-in-time universe",
               None: f"{which} could not all be scored on the point-in-time universe"}[held])
    marks["s6"] = _neighbours(gate, kind, neighbours, prereg.neighbours if prereg is not None else None, tbill)

    checks, reasons = _gate_a(gate, rule, prereg, registry, first_run_at, source,
                              gate.cfg["sleeve"]["final_requires"])
    verdict = _verdict(marks, reasons)
    for k, m in marks.items():
        m["judged"] = k in required
    if tier == "evidence_based" and verdict != "pass":
        # decision 16 section 14: S3 and S4 are printed, not judged; the verdict says so and never says "pass"
        part = _verdict({k: marks[k] for k in required}, reasons)
        verdict = "evidence_based" if part == "pass" else part
    label = gate.evidence.get("label", EVIDENCE_LABEL) if verdict == "evidence_based" else None
    kw = gate.stats_kw
    stats = {"window": [a["net"].index[0].date().isoformat(), a["net"].index[-1].date().isoformat()],
             "kind": kind, "benchmark": gate.cfg["sleeve"]["benchmark"][kind],
             "mean_exposure": float(a["exposure"].mean()),
             "rule": mtm.curve(a["with_cash"], rf=a["rf"] if a["rf"] is not None else 0.0, **kw),
             "rule_cash_at_zero": mtm.curve(a["net"], **kw),
             "benchmark_curve": mtm.curve(a["benchmark"], rf=a["rf"] if a["rf"] is not None else 0.0, **kw),
             "spy": mtm.curve(a["spy"], rf=a["rf"] if a["rf"] is not None else 0.0, **kw),
             "alpha_beta": asdict(mtm.alpha_beta(a["with_cash"], a["spy"], periods=kw["periods"],
                                                 rf=a["rf"] if a["rf"] is not None else 0.0)),
             "cash": "Treasury bills on both sides" if a["rf"] is not None else
             "0% on both sides (no bill-rate file)",
             "calendar_years": mtm.calendar_years(a["with_cash"], a["benchmark"])}
    return _clean({"gate": "B-sleeve", "gate_version": 3, "rule": rule, "family": gate.family_of(rule),
                   "generated_at": _iso(as_of or datetime.now(UTC)), "gate_sha": gate.sha,
                   "prereg": prereg.summary() if prereg is not None else None, "gate_a": checks,
                   "source": dict(source or {}), "sessions": int(a["net"].size),
                   "sessions_before_window_dropped": a["dropped_before"],
                   "sessions_in_holdout_dropped": a["dropped_holdout"], "marks": marks,
                   "provisional": bool(reasons), "provisional_reasons": reasons,
                   "verdict": verdict, "tier": tier, "label": label,
                   "needs_holdout_check": verdict in ("pass", "evidence_based"),
                   "can_pass": not reasons, "statistics": stats})


def _neighbours(gate: GateV3, kind: str, neighbours: Mapping[str, Account] | None,
                declared: tuple[str, ...] | None, tbill) -> dict:
    limit = {"each": "active return above zero", "neighbours_max": gate.cfg["gate_a"]["max_neighbours"]}
    neighbours = dict(neighbours or {})
    if declared is None:
        declared = tuple(neighbours)  # no pre-registration: score what was supplied; Gate A blocks a pass
        if not declared:
            return _mark(None, {"supplied": []}, limit, "no pre-registration and no neighbour was supplied")
    if not declared:
        return _mark(False, {"declared": []}, limit, "the pre-registration declares no neighbour: no plateau check")
    results, values, why = {}, {}, []
    for name in declared:
        acc = neighbours.get(name)
        if acc is None or acc.exposure is None:
            results[name] = None
            why.append(f"{name}: no result supplied" if acc is None else f"{name}: no exposure series")
            continue
        v = float(active_return(gate, kind, acc, tbill)["active"].mean() * gate.stats_kw["periods"])
        values[name], results[name] = v, bool(v > 0.0)
        if not results[name]:
            why.append(f"{name}: active return {v:.2%} a year")
    return _mark(_all(list(results.values())), {"declared": list(declared), "results": results,
                                                "active_return": values,
                                                "undeclared": sorted(set(neighbours) - set(declared))},
                 limit, "; ".join(why))


# ---------------------------------------------------------------- the book marks


def _cut_book(gate: GateV3, acc: Account):
    net, before, after = gate.cut(acc.net, "book returns")
    if net.size < 30:
        raise ValueError(f"{net.size} sessions on the research window: nothing to judge")
    net, spy = mtm.align(net, acc.spy)
    costs = mtm.align(net, mtm.dated(acc.costs, "costs"))[1] if acc.costs is not None else None
    return net, spy, costs, before, after


def b1(gate: GateV3, net: pd.Series) -> dict:
    """Net return positive overall and in at least 60% of the counted calendar years (gate v2 mark 1)."""
    m = gate.cfg["book"]["b1_return"]
    every = mtm.calendar_years(net)
    years = [y for y in every if not y["partial"] or y["months"] >= int(m["part_year_min_months"])]
    n, pos = len(years), sum(y["return"] > 0.0 for y in years)
    need = math.ceil(float(m["positive_years_share_min"]) * n - 1e-9)
    total = mtm.total_return(net)
    why = []
    if not total > 0.0:
        why.append("net return is not positive")
    if n < int(m["years_min"]):
        why.append(f"the test spans {n} counted calendar years, the mark needs {m['years_min']}")
    if pos < need:
        why.append(f"{pos} positive years, {need} needed")
    return _mark(not why, {"total_return": total, "positive_years": pos, "years": n,
                           "years_not_counted": [y["year"] for y in every if y not in years]},
                 {"total_return_above": 0.0, "positive_years_min": need, "years_min": int(m["years_min"])},
                 "; ".join(why))


def b2(gate: GateV3, net: pd.Series, spy: pd.Series, intervals: bool = True) -> dict:
    """EITHER CAGR >= SPY - 2 points with drawdown <= 0.75 x SPY's, OR CAGR >= SPY + 1 point with <= 1.1 x
    (gate v2 mark 2, unchanged). The paired bootstrap intervals are printed, not judged."""
    m = gate.cfg["book"]["b2_spy"]
    per = gate.stats_kw["periods"]
    c, cs = mtm.cagr(net, per), mtm.cagr(spy, per)
    dd, dds = mtm.max_drawdown(net), mtm.max_drawdown(spy)
    points = (c - cs) * 100.0
    branch = {k: bool(points >= b["cagr_vs_spy_points_min"] and dd <= b["max_drawdown_vs_spy_max"] * dds)
              for k, b in m.items()}
    numbers = {"cagr": c, "spy_cagr": cs, "cagr_vs_spy_points": points, "max_drawdown": dd,
               "spy_max_drawdown": dds, "max_drawdown_vs_spy": dd / dds if dds > 0 else math.inf, "branch": branch}
    if intervals:
        rows = bootstrap_paths(gate, net.size)
        a, s = net.to_numpy()[rows], spy.to_numpy()[rows]
        diff = (_path_cagr(a, per) - _path_cagr(s, per)) * 100.0
        dda, dds_ = mtm.path_max_drawdown(a), mtm.path_max_drawdown(s)
        ratio = np.where(dds_ > 0, dda / np.where(dds_ > 0, dds_, 1.0), np.nan)
        ok = {k: (diff >= b["cagr_vs_spy_points_min"]) & (dda <= b["max_drawdown_vs_spy_max"] * dds_)
              for k, b in m.items()}
        numbers.update(cagr_vs_spy_points_interval=_interval(gate, diff[np.isfinite(diff)]),
                       max_drawdown_vs_spy_interval=_interval(gate, ratio[np.isfinite(ratio)]),
                       interval_level=float(gate.cfg["bootstrap"]["interval"]),
                       bootstrap_share_meeting={**{k: float(v.mean()) for k, v in ok.items()},
                                                "either": float(np.logical_or.reduce(list(ok.values())).mean())},
                       bootstrap_share_shallower_drawdown=float((dda < dds_).mean()))
    met = any(branch.values())
    return _mark(met, numbers, {k: dict(b) for k, b in m.items()}, "" if met else "neither branch holds")


def _book_1_to_3(gate: GateV3, acc: Account) -> dict:
    net, spy, costs, _, _ = _cut_book(gate, acc)
    marks = {"b1": b1(gate, net), "b2": b2(gate, net, spy)}
    k = float(gate.cfg["book"]["b3_costs"]["costs_multiple"])
    if costs is None:
        marks["b3"] = _mark(None, {}, {"costs_multiple": k}, "no daily cost series was supplied")
    else:
        hard = pd.Series(mtm.at_cost_multiple(net, costs, k), index=net.index)
        m1, m2 = b1(gate, hard), b2(gate, hard, spy, intervals=False)
        met = bool(m1["met"] and m2["met"])
        marks["b3"] = _mark(met, {"b1": m1["met"], "b2": m2["met"], "cagr": m2["numbers"]["cagr"],
                                  "max_drawdown": m2["numbers"]["max_drawdown"],
                                  "breakeven_multiple": mtm.breakeven_cost_multiple(net, costs)},
                            {"costs_multiple": k}, "" if met else "; ".join(x for x in (m1["why"], m2["why"]) if x))
    return marks


def evaluate_book(gate: GateV3, *, book: str, account: Account, pit_account: Account | None = None,
                  neighbours: Mapping[str, pd.Series] | None = None, sleeves: Mapping[str, str] | None = None,
                  prereg: Prereg | None = None, source: Mapping[str, Any] | None = None,
                  registry: Registry | None = None, first_run_at: datetime | None = None,
                  tbill: pd.Series | None = None, as_of: datetime | None = None,
                  budgets: Mapping[str, float] | None = None) -> dict:
    """The book test: marks B1 to B6 for the whole account that would trade.

    account: the combined book's run through the engine (one account: shared cash, one per-name cap), not a
    weighted sum of sleeve results. neighbours: daily net returns of each pre-declared neighbour of the book.
    sleeves: the sleeve-test verdict of every return sleeve inside the book, by rule name; an index core and a
    risk overlay are part of the book's construction and are not listed (a book with no return sleeve passes B6
    with an empty mapping, which must be given explicitly). A sleeve's verdict `evidence_based` (decision 16
    section 14) is admitted when the book's entry under `books:` (or `budgets`) gives it a budget and all such
    sleeves together take at most `evidence_based.book_budget_max_pct` of the book."""
    net, spy, costs, before, after = _cut_book(gate, account)
    marks = _book_1_to_3(gate, account)
    want = gate.cfg["book"]["b4_universe"]
    if account.universe == want:
        got, where = {k: marks[k]["met"] for k in ("b1", "b2", "b3")}, "this run is on the point-in-time universe"
    elif pit_account is not None and pit_account.universe == want:
        got = {k: v["met"] for k, v in _book_1_to_3(gate, pit_account).items()}
        where = "a second run on the point-in-time universe"
    else:
        got, where = None, ""
    marks["b4"] = _mark(None, {"universe": account.universe}, {"universe": want},
                        "no run on the point-in-time universe was supplied") if got is None else \
        _mark(_all(list(got.values())), {"universe": want, "source": where, **got}, {"universe": want},
              {True: "", False: "B1 to B3 do not all hold on the point-in-time universe",
               None: "B1 to B3 could not all be scored on the point-in-time universe"}[_all(list(got.values()))])
    declared = prereg.neighbours if prereg is not None else None
    nb = dict(neighbours or {})
    if declared is None:
        marks["b5"] = _mark(None, {"supplied": sorted(nb)}, {"neighbours_pass": "b1"},
                            "no pre-registration, so no neighbours were declared")
    elif not declared:
        marks["b5"] = _mark(False, {"declared": []}, {"neighbours_pass": "b1"},
                            "the pre-registration declares no neighbour: no plateau check")
    else:
        res = {n: (b1(gate, gate.cut(nb[n], n)[0])["met"] if n in nb else None) for n in declared}
        marks["b5"] = _mark(_all(list(res.values())), {"declared": list(declared), "results": res},
                            {"neighbours_pass": "b1"},
                            "; ".join(f"{n}: {'no result supplied' if v is None else 'fails B1'}"
                                      for n, v in res.items() if v is not True))
    cap = float(gate.evidence.get("book_budget_max_pct", 10.0))
    limit6 = {"every_return_sleeve": "pass", "or": "evidence_based", "evidence_based_budget_max_pct": cap}
    if sleeves is None:
        marks["b6"] = _mark(None, {}, limit6,
                            "the sleeve verdicts were not supplied (an empty mapping for a book without sleeves)")
    else:
        # QA #542: a listed book's budgets are its pre-registered record and cannot be replaced by the caller;
        # `budgets` is read only for a book not (yet) under `books:` (which is provisional by Gate A anyway)
        listed = gate.book(book)
        have = dict((listed.get("sleeves") or {}) if listed is not None else (budgets or {}))
        bad, eb = {}, {}
        for k, v in sleeves.items():
            if v == "pass":
                continue
            if v == "evidence_based" and gate.tier(k) != "evidence_based":
                # QA #542: the verdict string alone cannot admit a sleeve; its entry must take the tier (with
                # its evidence cited) in config/gate_v3.yaml
                bad[k] = "evidence_based, but its entry in config/gate_v3.yaml does not take the evidence-based tier"
            elif v == "evidence_based" and k in have:
                eb[k] = float(have[k])
            elif v == "evidence_based":
                bad[k] = "evidence_based, but the book gives it no budget"
            else:
                bad[k] = v
        total = sum(eb.values())
        why = [f"{k}: {v}" for k, v in bad.items()]
        if total > cap + 1e-9:
            why.append(f"evidence-based sleeves take {total:g}% of the book together, at most {cap:g}% is allowed")
        # QA #542: every return sleeve the book lists needs a verdict; a missing one leaves B6 unknown
        missing = sorted(set(have) - set(sleeves)) if listed is not None else []
        state = False if why else (None if missing else True)
        if missing:
            why.append("no sleeve verdict supplied for " + ", ".join(missing))
        label = gate.evidence.get("label", EVIDENCE_LABEL)
        marks["b6"] = _mark(state, {"sleeves": dict(sleeves), "evidence_based_budget_pct": total,
                                    "labels": {k: label for k in eb}, "missing_verdicts": missing},
                            limit6, "; ".join(why))
    checks, reasons = _gate_a(gate, book, prereg, registry, first_run_at, source,
                              gate.cfg["book"]["final_requires"], role="book")
    kw = gate.stats_kw
    stats = {"window": [net.index[0].date().isoformat(), net.index[-1].date().isoformat()],
             "book": mtm.curve(net, **kw), "spy": mtm.curve(spy, **kw),
             "alpha_beta": asdict(mtm.alpha_beta(net, spy, periods=kw["periods"])),
             "calendar_years": mtm.calendar_years(net, spy), "cash_yield": None}
    if tbill is not None:
        e = mtm.align(net, account.exposure)[1] if account.exposure is not None else None
        stats["cash_yield"] = mtm.cash_views(net, spy, e, mtm.tbill_daily(tbill, net.index), **kw)
    return _clean({"gate": "B-book", "gate_version": 3, "rule": book, "generated_at": _iso(as_of or
                                                                                          datetime.now(UTC)),
                   "gate_sha": gate.sha, "prereg": prereg.summary() if prereg is not None else None,
                   "gate_a": checks, "source": dict(source or {}), "sessions": int(net.size),
                   "sessions_before_window_dropped": before, "sessions_in_holdout_dropped": after, "marks": marks,
                   "provisional": bool(reasons), "provisional_reasons": reasons,
                   "verdict": _verdict(marks, reasons), "can_pass": not reasons, "statistics": stats})


# ---------------------------------------------------------------- the final check on the hold-out


def holdout_check(gate: GateV3, *, research: pd.Series, holdout: pd.Series, first_run_at: datetime | None,
                  spy_research: pd.Series | None = None, spy_holdout: pd.Series | None = None) -> dict:
    """Does the hold-out look like the research window? (decision 16 section 6)

    Sleeve (no SPY given): `research` and `holdout` are the ACTIVE return series; the hold-out's annualised mean
    must be at or above the 5th percentile of stationary-bootstrap stretches of the same length drawn from the
    research window. Book (SPY given for both): the same for CAGR minus SPY's in points, and the hold-out's
    maximum drawdown must be at or below the 95th percentile of the resampled drawdowns.

    `clean` is False when the rule was first run before the hold-out was frozen: the hold-out had then already
    been seen, and the result is a consistency check, never an out-of-sample test."""
    band = float(gate.cfg["holdout_check"]["band"])
    per = gate.stats_kw["periods"]
    r, h = mtm.dated(research, "research"), mtm.dated(holdout, "hold-out")
    lo, hi = gate.window
    if (h.index <= hi).any() or (r.index > hi).any() or h.size < 20:
        raise ValueError("the research series must end by the research window's last session and the hold-out "
                         "series (at least 20 sessions) must start after it")
    rows = bootstrap_paths(gate, r.size, h.size)
    frozen = pd.Timestamp(str(gate.cfg["holdout_check"]["contaminated_before"]))
    clean = first_run_at is not None and pd.Timestamp(_utc(first_run_at).date()) >= frozen
    out: dict = {"sessions": int(h.size), "band": band, "clean": bool(clean),
                 "label": "out-of-sample check" if clean else
                 "consistency check: the rule was first run before the hold-out was frozen", "checks": {}}
    if spy_research is None:
        boot = r.to_numpy()[rows].mean(axis=1) * per
        got, floor = float(h.mean() * per), float(np.quantile(boot, band))
        out["checks"]["active_return"] = {"holdout": got, "floor": floor, "research": float(r.mean() * per),
                                          "met": bool(got >= floor)}
    else:
        sr, sh = mtm.align(r, spy_research)[1], mtm.align(h, spy_holdout)[1]
        a, s = r.to_numpy()[rows], sr.to_numpy()[rows]
        diff = (_path_cagr(a, per) - _path_cagr(s, per)) * 100.0
        got = (mtm.cagr(h, per) - mtm.cagr(sh, per)) * 100.0
        floor = float(np.nanquantile(diff, band))
        dd, cap = mtm.max_drawdown(h), float(np.quantile(mtm.path_max_drawdown(a), 1.0 - band))
        out["checks"] = {"cagr_vs_spy_points": {"holdout": got, "floor": floor, "met": bool(got >= floor)},
                         "max_drawdown": {"holdout": dd, "ceiling": cap, "met": bool(dd <= cap)}}
    out["met"] = all(c["met"] for c in out["checks"].values())
    return _clean(out)


# ---------------------------------------------------------------- Markdown


SLEEVE_LABELS = {"s1": "S1. Return above the same amount of SPY", "s2": "S2. The same at doubled costs",
                 "s3": "S3. Skill against chance, corrected across rule families",
                 "s4": "S4. Deflated Sharpe against the family's own configurations",
                 "s5": "S5. Holds on the point-in-time universe", "s6": "S6. Every neighbour is above zero too"}
BOOK_LABELS = {"b1": "B1. Net return positive overall and in enough calendar years",
               "b2": "B2. CAGR and drawdown against SPY", "b3": "B3. B1 and B2 at doubled costs",
               "b4": "B4. Holds on the point-in-time universe", "b5": "B5. Every neighbour passes B1",
               "b6": "B6. Every return sleeve in the book has passed the sleeve test"}


def _head(rep: dict, title: str, labels: Mapping[str, str]) -> list[str]:
    st, m = rep["statistics"], rep["marks"]
    out = [f"# {title}: {rep['rule']} ({st['window'][0]} to {st['window'][1]})", "",
           f"**Verdict: {rep['verdict']}**" + (" (cannot pass)" if not rep["can_pass"] else ""), ""]
    if rep["provisional_reasons"]:
        out += ["This run cannot pass, whatever the marks say:", ""]
        out += [f"- {r}" for r in rep["provisional_reasons"]] + [""]
    out += ["| Mark | Result | Why |", "|---|---|---|"]
    out += [f"| {labels[k]} | {_state(m[k]['met'])} | {m[k]['why'] or '-'} |" for k in labels]
    if rep["sessions_in_holdout_dropped"]:
        out += ["", f"{rep['sessions_in_holdout_dropped']} sessions of the input lie in the hold-out. They were "
                    "dropped before any number was computed."]
    return out


def render_sleeve(rep: dict) -> str:
    """The sleeve test as Markdown."""
    st, m = rep["statistics"], rep["marks"]
    out = _head(rep, "Gate v3, sleeve test", SLEEVE_LABELS)
    if rep.get("tier") == "evidence_based":
        out += ["", "Tier: evidence-based (decision 16 section 14). S3 and S4 are printed, not judged."
                    + (f" This sleeve is **{rep['label']}**: it has not passed gate v3." if rep.get("label") else "")]
    n1 = m["s1"]["numbers"]
    out += ["", f"- Family: {rep['family'] or 'none'}. Kind: {st['kind']} (benchmark: SPY, {st['benchmark']}; "
                f"cash: {st['cash']}). Mean exposure {_pct(st['mean_exposure'], 0)}.",
            f"- Active return: {_pct(n1['annualised'])} a year, t {n1['t']:.2f} (Newey-West, {n1['lags']} lags), "
            f"{n1['interval_level']:.0%} bootstrap interval {_pct(n1['interval'][0])} to {_pct(n1['interval'][1])}; "
            f"tracking error {_pct(n1['tracking_error'])}, information ratio {n1['information_ratio']:.2f}.",
            f"- Rule: CAGR {_pct(st['rule']['cagr'])}, max drawdown {_pct(st['rule']['max_drawdown'])}. Benchmark: "
            f"CAGR {_pct(st['benchmark_curve']['cagr'])}, max drawdown {_pct(st['benchmark_curve']['max_drawdown'])}. "
            f"SPY: CAGR {_pct(st['spy']['cagr'])}, max drawdown {_pct(st['spy']['max_drawdown'])}.",
            f"- Alpha against SPY: {_pct(st['alpha_beta']['alpha'])} a year, t {st['alpha_beta']['alpha_t']:.2f}; "
            f"beta {st['alpha_beta']['beta']:.2f}."]
    n2 = m["s2"]["numbers"]
    if n2:
        out.append("- Active return by cost multiple: " + ", ".join(
            f"{k} {_pct(v)}" for k, v in n2["active_return_at"].items()) + ".")
    n3 = m["s3"]["numbers"]
    if n3.get("p_value") is not None:
        line = (f"- Control: percentile {n3['percentile']:.1f} of {n3['runs']} runs, p {n3['p_value']:.4f}, drew "
                f"from {n3.get('drew_from') or 'an unstated pool'}")
        if n3.get("holm_adjusted") is not None:
            line += (f"; family p {n3['family_p']:.4f} ({n3['family_members']} rules in the family), Holm-adjusted "
                     f"{n3['holm_adjusted']:.4f} over {m['s3']['limit']['families']} families; "
                     f"Benjamini-Hochberg-adjusted {n3['bh_adjusted']:.4f} (reported)")
        dp = n3.get("drawdown_percentile")
        out.append(line + (f"; drawdown percentile {dp:.1f}" if dp is not None else
                           "; drawdown percentile not in the report") + ".")
    n4 = m["s4"]["numbers"]
    if n4.get("dsr") is not None:
        out.append(f"- Deflated Sharpe {n4['dsr']:.3f} against {n4['configurations']:,} configurations of the "
                   f"family (the whole registry holds {n4['whole_registry_configurations']:,}).")
    out += ["", f"gate_v3.yaml sha256 `{rep['gate_sha']}`. Generated {rep['generated_at']}."]
    return "\n".join(out) + "\n"


def render_book(rep: dict) -> str:
    """The book test as Markdown."""
    m = rep["marks"]
    out = _head(rep, "Gate v3, book test", BOOK_LABELS)
    n = m["b2"]["numbers"]
    out += ["", "| Daily mark-to-market, net | Book | SPY total return |", "|---|---|---|",
            f"| CAGR | {_pct(n['cagr'])} | {_pct(n['spy_cagr'])} |",
            f"| Max drawdown | {_pct(n['max_drawdown'])} | {_pct(n['spy_max_drawdown'])} |", ""]
    if "cagr_vs_spy_points_interval" in n:
        lo, hi = n["cagr_vs_spy_points_interval"]
        dlo, dhi = n["max_drawdown_vs_spy_interval"]
        out += [f"- CAGR minus SPY's: {n['cagr_vs_spy_points']:.2f} points ({n['interval_level']:.0%} bootstrap "
                f"interval {lo:.2f} to {hi:.2f}). Drawdown over SPY's: {n['max_drawdown_vs_spy']:.2f} ({dlo:.2f} to "
                f"{dhi:.2f}).",
                f"- Share of resampled decades that meet B2: {_pct(n['bootstrap_share_meeting']['either'], 0)}; "
                f"with a shallower drawdown than SPY: {_pct(n['bootstrap_share_shallower_drawdown'], 0)}."]
    out += ["", f"gate_v3.yaml sha256 `{rep['gate_sha']}`. Generated {rep['generated_at']}."]
    return "\n".join(out) + "\n"
