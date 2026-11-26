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

Gate v3.1 (decision 18, M17 ticket 6.1, #701): book configurations are counted per book IDEA (`books.<book>.idea`;
every book of one idea shares the count), and B2 also needs its chance test (`book.b2_chance`): a deflated test of
the book's daily Sharpe over SPY's, with N = the idea's configurations. B2 holds only when a branch holds AND the
chance test holds, so no verdict can turn from fail to pass. Any amendment of the pre-registration committed after
the first logged run is "hindsight", and the verdict says so (`hindsight`, `verdict_text`).

Every series is cut to the research window first. Sessions in the hold-out are dropped before any number is
computed and are counted in the report (`sessions_in_holdout_dropped`).
"""

from __future__ import annotations

import hashlib
import math
import re
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
from kaibot.evaluation.metrics import expected_max_sharpe, norm_cdf
from kaibot.evaluation.prereg import Prereg
from kaibot.evaluation.random_control import p_from_percentile
from kaibot.evaluation.trials import Registry, count_configurations

ROOT = Path(__file__).resolve().parents[3]
REQUIRED = {
    "window": ("research_from", "research_to"),
    "gate_a": ("max_neighbours", "must_declare"),
    "sleeve": ("benchmark", "s1_active_return", "s2_costs", "s3_control", "s4_deflated_sharpe", "s5_universe",
               "s6_neighbours", "final_requires"),
    "book": ("b1_return", "b2_spy", "b2_chance", "b3_costs", "b4_universe", "b5_neighbours", "b6_sleeves",
             "final_requires"),
    "holdout_check": ("band", "contaminated_before"),
    "bootstrap": ("method", "mean_block_sessions", "runs", "seed", "interval"),
    "statistics": ("periods_per_year", "sharpe_haircut", "sharpe_warning_above"),
}
KINDS = ("selection", "timing")
TIERS = ("full", "evidence_based")
EVIDENCE_LABEL = "evidence-based, not proven here"
EVIDENCE_BUDGET_MAX_PCT = 10.0  # decision 16 s.14: all evidence-based sleeves of a book; config may only lower it
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
            if "idea" in b and not (isinstance(b["idea"], str) and re.fullmatch(r"[a-z][a-z0-9_]*", b["idea"])):
                raise ValueError(f"config/gate_v3.yaml: books.{name}.idea must be a name (lower-case letters, "
                                 "digits and underscores)")
            for rule, pct in b["sleeves"].items():
                if isinstance(pct, bool) or not isinstance(pct, int | float) or not 0 < float(pct) <= 100:
                    raise ValueError(f"config/gate_v3.yaml: books.{name}.sleeves.{rule}: the budget must be a "
                                     "number in (0, 100] (% of the book)")
        cap = (cfg.get("evidence_based") or {}).get("book_budget_max_pct", EVIDENCE_BUDGET_MAX_PCT)
        if isinstance(cap, bool) or not isinstance(cap, int | float) or not 0 <= float(cap) <= EVIDENCE_BUDGET_MAX_PCT:
            raise ValueError(f"config/gate_v3.yaml: evidence_based.book_budget_max_pct={cap!r} must be a number in "
                             f"[0, {EVIDENCE_BUDGET_MAX_PCT:g}] (decision 16 section 14; it may only be lowered)")
        s3c = cfg["sleeve"]["s3_control"]
        share = s3c.get("unfilled_slots_max_share", 0.0)
        if isinstance(share, bool) or not isinstance(share, int | float) or not 0.0 <= float(share) <= 0.005:
            raise ValueError("config/gate_v3.yaml: s3_control.unfilled_slots_max_share must be in [0, 0.005] "
                             "(decision 18 section 8; it may only be lowered)")
        if (s3c.get("holm_inputs") or "final_requires") != "final_requires":
            raise ValueError("config/gate_v3.yaml: s3_control.holm_inputs must be final_requires (decision 18 s.8)")
        needs = cfg["holdout_check"].get("clean_needs", ["first_run_after_freeze", "design_saw_holdout_false"])
        if sorted(needs or []) != ["design_saw_holdout_false", "first_run_after_freeze"]:
            raise ValueError("config/gate_v3.yaml: holdout_check.clean_needs must list first_run_after_freeze and "
                             "design_saw_holdout_false (decision 18 section 9)")
        if (cfg["sleeve"]["s4_deflated_sharpe"].get("trial_sharpes") or "active_return") != "active_return":
            raise ValueError("config/gate_v3.yaml: s4_deflated_sharpe.trial_sharpes must be active_return (decision "
                             "18 section 6: S4 tests the active return, so its variance comes from active Sharpes)")
        if cfg["bootstrap"]["method"] != "stationary":
            raise ValueError("config/gate_v3.yaml: only the stationary bootstrap is implemented")
        return cls(cfg, hashlib.sha256(raw).hexdigest())

    @property
    def version(self) -> str:
        return str(self.cfg.get("version"))

    def book_idea(self, name: str) -> str:
        """#701: the idea a book tests (`books.<book>.idea`, default the book's own name)."""
        return str((self.book(name) or {}).get("idea") or name)

    def books_of_idea(self, idea: str) -> list[str]:
        return sorted(n for n in (self.cfg.get("books") or {}) if self.book_idea(n) == idea)

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
    # #703 (decision 18 s.7): the time-weighted share of equity invested during each session's minutes. An intraday
    # rule is flat at every close, so `exposure` alone makes its timing benchmark all T-bills
    intraday_exposure: pd.Series | None = None


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
    gap = c.get("committed_exposure_gap_pts") if isinstance(c.get("committed_exposure_gap_pts"), Mapping) else {}
    unfilled = inner.get("unfilled", c.get("unfilled"))
    return {"percentile": None if pct is None else float(pct), "runs": runs, "matched": bool(c.get("matched")),
            # #704: what the control tolerance reads (decision 18 s.8)
            "slots": c.get("slots", inner.get("slots")), "unfilled": None if unfilled is None else int(unfilled),
            "exposure_gap_max_pts": gap.get("max"), "exposure_tolerance_pts": c.get("exposure_tolerance_pts"),
            "p_value": None if p is None else float(p), "kind": c.get("kind"),
            "pool": c.get("pool", inner.get("pool")),
            "drawdown_percentile": c.get("drawdown_percentile", inner.get("drawdown_percentile"))}


def control_matched(gate: GateV3, n: Mapping) -> tuple[bool, str]:
    """#704 (decision 18 s.8): the control is matched as stored, or within the pre-registered tolerance: at most
    `unfilled_slots_max_share` of its slots left unfilled over ALL runs together (so no single run left more), with
    the money committed per session still within the control's own exposure tolerance in every run. (matched, the
    reading used)."""
    if n.get("matched"):
        return True, "matched"
    share = gate.cfg["sleeve"]["s3_control"].get("unfilled_slots_max_share")
    slots, unfilled = n.get("slots"), n.get("unfilled")
    gap, tol = n.get("exposure_gap_max_pts"), n.get("exposure_tolerance_pts")
    if share is None or not slots or unfilled is None or gap is None or tol is None:
        return False, "the control did not fill every slot, so it is not matched"
    allowed = math.floor(float(share) * int(slots) + 1e-9)
    if 0 < unfilled <= allowed and float(gap) <= float(tol):
        return True, (f"{unfilled} of {slots} slots unfilled over all runs, within the tolerance of {allowed} "
                      f"({float(share):.1%}); committed exposure within {float(tol):g} pts in every run")
    if float(gap) > float(tol):
        return False, f"the control's committed exposure is off by up to {float(gap):g} pts, more than {float(tol):g}"
    return False, (f"the control left {unfilled} of {slots} slots unfilled over all runs, more than the tolerance "
                   f"of {allowed} ({float(share):.1%})")


def holm_input(gate: GateV3, rule: str, control: Any, source: Mapping | None, holdout_sessions: int,
               control_source: str | None = None) -> tuple[float | None, list[str]]:
    """#704 (decision 18 s.8): (the rule's control p-value, the reasons it may NOT enter Holm). Only a p-value from
    a run that meets `sleeve.final_requires` (the engine with SimBroker, cost model v2, data repairs, SPY total
    return), with enough control runs for its family, a matched control (within the tolerance) drawn from the
    rule's own pool or random times, and a window that stays on the research window enters the correction. Any
    other p-value is shown greyed out and counts as 1 in Holm."""
    n = control_numbers(control)
    if n is None or n["p_value"] is None:
        return None, ["no control p-value"]
    why = [f"{k} is {(source or {}).get(k, 'not stated')!r}, Holm needs {v!r}"
           for k, v in gate.cfg["sleeve"]["final_requires"].items() if (source or {}).get(k) != v]
    need = control_runs_for(gate, rule)
    if n["runs"] < need:
        why.append(f"{n['runs']} control runs, {need} needed")
    ok, how = control_matched(gate, n)
    if not ok:
        why.append(how)
    entry = gate.rule(rule) or {}
    timing = entry.get("kind") == "timing" and str(n.get("kind") or "").startswith("random_timing")
    drew = n["pool"] if n["pool"] not in (None, "universe") else (control_source or entry.get("existing_control"))
    if not timing and drew not in SCORABLE_CONTROLS:
        why.append(f"the control drew from {drew or 'an unstated pool'}, not the rule's own candidate pool")
    if holdout_sessions:
        why.append(f"its window ran {holdout_sessions} sessions into the hold-out")
    return n["p_value"], why


def control_runs_for(gate: GateV3, rule: str) -> int:
    """#547: the control runs S3 needs for `rule` (decision 16 section 15): `random_runs_min_several_rules` (2,000)
    when its family lists two or more rules, `random_runs_min` (500) for a one-rule family or a rule in no family.
    The research command and `daily_report.report` take their default from here (scripts/ keep their own)."""
    c = gate.cfg["sleeve"]["s3_control"]
    fam = gate.family_of(rule)
    if fam is not None and len(gate.families[fam]["rules"]) > 1:
        return int(c.get("random_runs_min_several_rules", c["random_runs_min"]))
    return int(c["random_runs_min"])


def default_control_runs(rule: str, root: Path | str | None = None) -> int:
    """#560: `control_runs_for` on the committed config/gate_v3.yaml, for a run script's `--control-runs` default."""
    return control_runs_for(GateV3.load(root), rule)


def book_budget_problems(gate: GateV3, book: str, ran: Mapping[str, float]) -> list[str]:
    """#547: the budgets the engine ran (sleeve -> % of the book) against the book's pre-registered entry under
    `books:`. Empty when they match; otherwise one line per difference. A book not listed is a difference too."""
    entry = gate.book(book)
    if entry is None:
        return [f"{book} is not listed under `books:` of config/gate_v3.yaml"]
    want = {k: float(v) for k, v in (entry.get("sleeves") or {}).items()}
    got = {k: float(v) for k, v in ran.items()}
    out = [f"{k}: pre-registered {want[k]:g}% of the book, the engine ran {got[k]:g}%"
           for k in sorted(set(want) & set(got)) if abs(want[k] - got[k]) > 1e-9]
    out += [f"{k}: pre-registered at {want[k]:g}%, not in the run" for k in sorted(set(want) - set(got))]
    out += [f"{k}: in the run at {got[k]:g}%, not pre-registered" for k in sorted(set(got) - set(want))]
    return out


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
    skill, so it must not be copied. Gate v3.1 (`average_matched_time_weighted`, #703): `exposure` is then the
    session's time-weighted exposure (the larger of the close exposure and the intraday one, see `active_return`),
    so an intraday rule is compared with the same average amount of SPY, not with T-bills.

    `prior` (amendment 1): the exposure at the close before the first session (0 when the account starts on it),
    so a sleeve invested going into the window is compared with SPY held at that exposure on its first session."""
    how = gate.cfg["sleeve"]["benchmark"][kind]
    e = exposure.to_numpy(dtype=float)
    if how not in ("path_matched", "average_matched", "average_matched_time_weighted"):
        raise ValueError(f"config/gate_v3.yaml: unknown benchmark {how!r}")
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
    tw = None
    if acc.intraday_exposure is not None:
        tw = mtm.align(net, mtm.dated(acc.intraday_exposure, "intraday exposure"))[1].clip(0.0, 1.0)
    timed = e  # what the timing benchmark averages
    if kind == "timing" and gate.cfg["sleeve"]["benchmark"]["timing"] == "average_matched_time_weighted" \
            and tw is not None:
        # #703: a session's exposure is the larger of the close exposure (held overnight) and the time-weighted
        # share of the session's minutes the rule was invested; SPY close to close at that average share. That
        # charges an intraday rule SPY's overnight drift too, so it is the stricter of the two readings
        timed = pd.Series(np.maximum(e.to_numpy(dtype=float), tw.to_numpy(dtype=float)), index=e.index)
    # the sleeve's idle cash earns bills; time invested during the session is not idle (#703), so an intraday rule
    # is not credited a full day's bill rate on money it had in SPY
    idle = 1.0 - (held if tw is None else np.maximum(held, tw.to_numpy(dtype=float)))
    mine = net if rf is None else pd.Series(net.to_numpy() + np.clip(idle, 0.0, 1.0) * rf.to_numpy(),
                                            index=net.index)
    bench = benchmark(gate, kind, spy, timed, rf, prior=prior)
    costs = mtm.align(net, mtm.dated(acc.costs, "costs"))[1] if acc.costs is not None else None
    return {"active": mine - bench, "net": net, "with_cash": mine, "benchmark": bench, "spy": spy, "exposure": e,
            "held": held, "intraday_exposure": tw, "benchmark_exposure": timed,
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


def _first_logged(registry: Registry | None, names: Sequence[str]) -> datetime | None:
    """The time of the earliest logged run of any of `names` in the registry (rows with an ISO `ts`)."""
    out = None
    for r in (registry.rows if registry is not None else ()):
        if r.get("strategy") in names and r.get("ts"):
            try:
                t = _utc(datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")))
            except ValueError:
                continue
            out = t if out is None or t < out else out
    return out


def hindsight(prereg: Prereg | None, first_run_at: datetime | None) -> list[dict]:
    """#701 (decision 18): the amendments of the pre-registration that were committed after the first run, so
    whoever wrote them could have seen results. An amendment whose commit time is not known (git not asked) is
    listed too: it is not shown to precede the run. Empty when there is no pre-registration or no run time."""
    if prereg is None or first_run_at is None:
        return []
    first = _utc(first_run_at)
    when = dict(prereg.amendment_log)
    out = []
    for head in prereg.amendments:
        t = when.get(head)
        if t is None:
            out.append({"amendment": head, "committed_at": None,
                        "why": "its commit time is not known, so it is not shown to precede the first run"})
        elif _utc(t) > first:
            out.append({"amendment": head, "committed_at": _iso(t),
                        "why": f"committed {_iso(t)}, after the first logged run ({_iso(first)})"})
    return out


def verdict_text(verdict: str, late: Sequence[Mapping]) -> str:
    """The verdict as printed: "<verdict>, hindsight (Amendment 4)" when an amendment came after the first run."""
    if not late:
        return verdict
    nums = [m.group(1) for a in late if (m := re.match(r"##\s+Amendment\s+(\d+)", str(a["amendment"])))]
    which = (("Amendments " if len(nums) > 1 else "Amendment ") + ", ".join(nums)) if nums else \
        f"{len(late)} amendment(s)"
    return f"{verdict}, hindsight ({which} written after the first run)"


def book_configurations(gate: GateV3, book: str, registry: Registry | None, unlogged: int = 0) -> dict:
    """#701 (decision 18): the configurations tried for the book's IDEA: every book listed with the same `idea`,
    counted with `count_configurations` (not rows), plus `unlogged` (a run being judged before its rows are
    appended). `configurations` is None without a registry."""
    idea = gate.book_idea(book)
    books = sorted(set(gate.books_of_idea(idea)) | {book})
    n = None if registry is None else \
        count_configurations([r for r in registry.rows if r.get("strategy") in books]) + int(unlogged)
    return {"idea": idea, "books": books, "configurations": n}


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
    ok, how = control_matched(gate, n)  # #704: within the pre-registered tolerance
    n["matched_reading"] = how
    if not ok:
        return _mark(None, n, limit, how)
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
    """Deflated Sharpe of the active return against the family's own configurations.

    Gate v3.1 (#702, decision 18 s.6): V is the variance of the family's ACTIVE-return trial Sharpes
    (`Registry.active_daily_sharpes`), the same series S4 tests. A trial that logged only an absolute-return Sharpe
    (every row before gate v3.1) is counted in N and flagged in the numbers, never put into V. With fewer than two
    active Sharpes V is unknown and the 1/(T-1) floor is used, as amendment 1 says."""
    lim = float(gate.cfg["sleeve"]["s4_deflated_sharpe"]["dsr_min"])
    limit = {"dsr_min": lim, "trials": "family"}
    fam = gate.family_of(rule)
    if registry is None or fam is None:
        return _mark(None, {}, limit, "no trial registry was supplied" if fam else "the rule is in no family")
    members = set(gate.families[fam]["rules"])
    rows = [r for r in registry.rows if r.get("strategy") in members]
    n = count_configurations(rows)
    sharpes = [s for m in members for s in registry.active_daily_sharpes().get(m, [])]
    flagged = sum(n for m, n in registry.absolute_only().items() if m in members)
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
                       else 0, "trial_sharpe_series": "active_return",
                       "trial_sharpes_absolute_only_flagged": flagged,
                       "whole_registry_configurations": registry.configurations, "sessions_judged": t,
                       "variance_of_trial_sharpes": var, "variance_floor": floor,
                       "variance_floor_binds": bool(floor is not None and (var is None or var < floor))},
                 limit, "" if met else f"deflated Sharpe {d.dsr:.3f} is below {lim:g}")


def active_sharpe_ann(gate: GateV3, rule: str, account: Account, tbill: pd.Series | None = None,
                      kind: str | None = None) -> float | None:
    """#702: the annualised Sharpe of `account`'s active return on the research window, as S4 tests it, for the
    trial row (`metrics.daily_active_sharpe_ann`). None when it cannot be computed (no exposure, too short)."""
    kind = kind or (gate.rule(rule) or {}).get("kind") or "selection"
    try:
        a = active_return(gate, kind, account, tbill)["active"].to_numpy(dtype=float)
    except ValueError:
        return None
    sd = float(a.std(ddof=1))
    return float(a.mean() / sd * math.sqrt(gate.stats_kw["periods"])) if sd > 0 else 0.0


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
    first = [t for t in (first_run_at and _utc(first_run_at), _first_logged(registry, [rule])) if t]
    late = hindsight(prereg, min(first) if first else None)
    kw = gate.stats_kw
    stats = {"window": [a["net"].index[0].date().isoformat(), a["net"].index[-1].date().isoformat()],
             "kind": kind, "benchmark": gate.cfg["sleeve"]["benchmark"][kind],
             "mean_exposure": float(a["benchmark_exposure"].mean()),  # #703: what the benchmark holds
             "mean_close_exposure": float(a["exposure"].mean()),
             "mean_intraday_exposure": None if a["intraday_exposure"] is None else
             float(a["intraday_exposure"].mean()),
             "rule": mtm.curve(a["with_cash"], rf=a["rf"] if a["rf"] is not None else 0.0, **kw),
             "rule_cash_at_zero": mtm.curve(a["net"], **kw),
             "benchmark_curve": mtm.curve(a["benchmark"], rf=a["rf"] if a["rf"] is not None else 0.0, **kw),
             "spy": mtm.curve(a["spy"], rf=a["rf"] if a["rf"] is not None else 0.0, **kw),
             "alpha_beta": asdict(mtm.alpha_beta(a["with_cash"], a["spy"], periods=kw["periods"],
                                                 rf=a["rf"] if a["rf"] is not None else 0.0)),
             "cash": "Treasury bills on both sides" if a["rf"] is not None else
             "0% on both sides (no bill-rate file)",
             "calendar_years": mtm.calendar_years(a["with_cash"], a["benchmark"])}
    return _clean({"gate": "B-sleeve", "gate_version": gate.version, "rule": rule, "family": gate.family_of(rule),
                   "generated_at": _iso(as_of or datetime.now(UTC)), "gate_sha": gate.sha,
                   "prereg": prereg.summary() if prereg is not None else None, "gate_a": checks,
                   "source": dict(source or {}), "sessions": int(a["net"].size),
                   "sessions_before_window_dropped": a["dropped_before"],
                   "sessions_in_holdout_dropped": a["dropped_holdout"], "marks": marks,
                   "provisional": bool(reasons), "provisional_reasons": reasons,
                   "verdict": verdict, "verdict_text": verdict_text(verdict, late), "hindsight": late,
                   "tier": tier, "label": label,
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


def _sharpe_rows(x: np.ndarray) -> np.ndarray:
    sd = x.std(axis=-1, ddof=1)
    return np.where(sd > 0, x.mean(axis=-1) / np.where(sd > 0, sd, 1.0), 0.0)


def b2_chance(gate: GateV3, net: pd.Series, spy: pd.Series, configurations: int | None) -> dict:
    """#701 (decision 18): is the book's daily Sharpe above SPY's by more than the best of N no-skill book
    configurations would show? N = the configurations of the book's idea.

        d = SR(book) - SR(SPY) (daily, idle cash at 0% as judged); V = variance of d over the gate's paired
        stationary-bootstrap resamples; SR0 = expected_max_sharpe(N, V); DSR = Z((d - SR0) / sqrt(V)).

    The null is "no Sharpe gain over SPY": a de-levered SPY has SPY's Sharpe, so it is what a book with no timing
    skill shows. V from the resamples is this statistic's own sampling variance, the counterpart of S4's 1/(T-1)
    floor. Holds when DSR >= `dsr_min`. Unknown when N is not known (no registry, or no logged configuration)."""
    c = gate.cfg["book"]["b2_chance"]
    lim = float(c["dsr_min"])
    limit = {"dsr_min": lim, "statistic": c.get("statistic"), "trials": c.get("trials")}
    a, s = net.to_numpy(dtype=float), spy.to_numpy(dtype=float)
    d = float(_sharpe_rows(a) - _sharpe_rows(s))
    rows = bootstrap_paths(gate, a.size)
    boot = _sharpe_rows(a[rows]) - _sharpe_rows(s[rows])
    v = float(np.var(boot, ddof=1))
    per = gate.stats_kw["periods"]
    nums = {"sharpe_book": float(_sharpe_rows(a)) * math.sqrt(per), "sharpe_spy": float(_sharpe_rows(s)) *
            math.sqrt(per), "sharpe_over_spy": d * math.sqrt(per), "se": math.sqrt(v) * math.sqrt(per),
            "configurations": configurations}
    if configurations is None or configurations < 1:
        return _mark(None, nums, limit, "the trial registry holds no configuration of this book's idea, so the "
                                        "chance test cannot count N")
    sr0 = expected_max_sharpe(int(configurations), v)
    tiny = math.sqrt(v) < 1e-9  # a book that is a fixed share of SPY: no spread, and no Sharpe gain to test
    dsr = float(d - sr0 > 1e-12) if tiny else norm_cdf((d - sr0) / math.sqrt(v))
    met = bool(dsr >= lim)
    nums.update(hurdle=sr0 * math.sqrt(per), dsr=float(dsr),
                psr=float(d > 1e-12) if tiny else float(norm_cdf(d / math.sqrt(v))))
    return _mark(met, nums, limit, "" if met else
                 f"deflated Sharpe over SPY's {dsr:.3f} is below {lim:g} (N = {configurations} configurations)")


def b2(gate: GateV3, net: pd.Series, spy: pd.Series, intervals: bool = True,
       configurations: int | None = None) -> dict:
    """EITHER CAGR >= SPY - 2 points with drawdown <= 0.75 x SPY's, OR CAGR >= SPY + 1 point with <= 1.1 x
    (gate v2 mark 2, unchanged), AND (gate v3.1, #701) the chance test `b2_chance` with N = `configurations`.
    A branch alone never passes B2 any more. The paired bootstrap intervals are printed, not judged."""
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
    chance = b2_chance(gate, net, spy, configurations)
    numbers["chance"] = {"met": chance["met"], **chance["numbers"], "why": chance["why"]}
    held = any(branch.values())
    met = _all([held, chance["met"]])
    why = [] if held else ["neither branch holds"]
    if chance["met"] is not True:
        why.append(chance["why"])
    return _mark(met, numbers, {**{k: dict(b) for k, b in m.items()}, "chance": chance["limit"]}, "; ".join(why))


def _book_1_to_3(gate: GateV3, acc: Account, configurations: int | None = None) -> dict:
    net, spy, costs, _, _ = _cut_book(gate, acc)
    marks = {"b1": b1(gate, net), "b2": b2(gate, net, spy, configurations=configurations)}
    k = float(gate.cfg["book"]["b3_costs"]["costs_multiple"])
    if costs is None:
        marks["b3"] = _mark(None, {}, {"costs_multiple": k}, "no daily cost series was supplied")
    else:
        hard = pd.Series(mtm.at_cost_multiple(net, costs, k), index=net.index)
        m1, m2 = b1(gate, hard), b2(gate, hard, spy, intervals=False, configurations=configurations)
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
                  budgets: Mapping[str, float] | None = None,
                  ran_budgets: Mapping[str, float] | None = None, unlogged_configurations: int = 0) -> dict:
    """The book test: marks B1 to B6 for the whole account that would trade.

    account: the combined book's run through the engine (one account: shared cash, one per-name cap), not a
    weighted sum of sleeve results. neighbours: daily net returns of each pre-declared neighbour of the book.
    sleeves: the sleeve-test verdict of every return sleeve inside the book, by rule name; an index core and a
    risk overlay are part of the book's construction and are not listed (a book with no return sleeve passes B6
    with an empty mapping, which must be given explicitly). A sleeve's verdict `evidence_based` (decision 16
    section 14) is admitted when the book's entry under `books:` (or `budgets`) gives it a budget and all such
    sleeves together take at most `evidence_based.book_budget_max_pct` of the book.

    Gate v3.1 (#701): B2 and B3 need the chance test with N = the configurations of the book's idea in `registry`,
    plus `unlogged_configurations` (a run judged before its own rows are appended). No registry: B2 is unknown."""
    net, spy, costs, before, after = _cut_book(gate, account)
    n_book = book_configurations(gate, book, registry, unlogged_configurations)
    marks = _book_1_to_3(gate, account, n_book["configurations"])
    want = gate.cfg["book"]["b4_universe"]
    if account.universe == want:
        got, where = {k: marks[k]["met"] for k in ("b1", "b2", "b3")}, "this run is on the point-in-time universe"
    elif pit_account is not None and pit_account.universe == want:
        got = {k: v["met"] for k, v in _book_1_to_3(gate, pit_account, n_book["configurations"]).items()}
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
    cap = min(float(gate.evidence.get("book_budget_max_pct", EVIDENCE_BUDGET_MAX_PCT)), EVIDENCE_BUDGET_MAX_PCT)
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
            if listed is not None and k not in have:
                # QA #553: a listed book's return sleeve needs a pre-registered budget, whatever its verdict
                bad[k] = f"{v}, but the book gives it no budget (none pre-registered under `books:`)"
                continue
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
    # #547: the budgets the engine actually ran must be the pre-registered ones; a book run at other weights is
    # another book, so B6 fails and the run cannot carry a verdict
    if ran_budgets is None and gate.book(book) is not None:
        # QA #553: a listed book cannot carry a verdict until the budgets it ran are checked against its record
        checks["budgets_as_pre_registered"] = None
        reasons.append("the budgets the engine ran were not supplied (`ran_budgets`), so they are not checked "
                       "against the book's pre-registered ones")
    if ran_budgets is not None:
        problems = book_budget_problems(gate, book, ran_budgets)
        checks["budgets_as_pre_registered"] = not problems
        if problems:
            reasons.append("the engine ran other sleeve budgets than the pre-registered ones: " + "; ".join(problems))
            m6 = marks["b6"]
            marks["b6"] = _mark(False, {**m6["numbers"], "ran_budgets": dict(ran_budgets)}, m6["limit"],
                                "; ".join(x for x in (m6["why"], *problems) if x))
    kw = gate.stats_kw
    stats = {"window": [net.index[0].date().isoformat(), net.index[-1].date().isoformat()],
             "book": mtm.curve(net, **kw), "spy": mtm.curve(spy, **kw),
             "alpha_beta": asdict(mtm.alpha_beta(net, spy, periods=kw["periods"])),
             "calendar_years": mtm.calendar_years(net, spy), "cash_yield": None}
    if tbill is not None:
        e = mtm.align(net, account.exposure)[1] if account.exposure is not None else None
        stats["cash_yield"] = mtm.cash_views(net, spy, e, mtm.tbill_daily(tbill, net.index), **kw)
    first = [t for t in (first_run_at and _utc(first_run_at), _first_logged(registry, n_book["books"])) if t]
    late = hindsight(prereg, min(first) if first else None)
    verdict = _verdict(marks, reasons)
    return _clean({"gate": "B-book", "gate_version": gate.version, "rule": book,
                   "generated_at": _iso(as_of or datetime.now(UTC)),
                   "gate_sha": gate.sha, "prereg": prereg.summary() if prereg is not None else None,
                   "gate_a": checks, "source": dict(source or {}), "sessions": int(net.size),
                   "sessions_before_window_dropped": before, "sessions_in_holdout_dropped": after, "marks": marks,
                   "provisional": bool(reasons), "provisional_reasons": reasons, "book_configurations": n_book,
                   "hindsight": late, "verdict": verdict, "verdict_text": verdict_text(verdict, late),
                   "can_pass": not reasons, "statistics": stats})


# ---------------------------------------------------------------- the final check on the hold-out


def holdout_check(gate: GateV3, *, research: pd.Series, holdout: pd.Series, first_run_at: datetime | None,
                  spy_research: pd.Series | None = None, spy_holdout: pd.Series | None = None,
                  design_saw_holdout: bool | None = None, prereg: Prereg | None = None) -> dict:
    """Does the hold-out look like the research window? (decision 16 section 6)

    Sleeve (no SPY given): `research` and `holdout` are the ACTIVE return series; the hold-out's annualised mean
    must be at or above the 5th percentile of stationary-bootstrap stretches of the same length drawn from the
    research window. Book (SPY given for both): the same for CAGR minus SPY's in points, and the hold-out's
    maximum drawdown must be at or below the 95th percentile of the resampled drawdowns.

    `clean` is False when the rule was first run before the hold-out was frozen: the hold-out had then already
    been seen, and the result is a consistency check, never an out-of-sample test.

    Gate v3.1 (#705, decision 18 s.9): `clean` also needs the pre-registration to declare
    `design_saw_holdout: false` (taken from `prereg` when given). True, or not declared, forces the
    consistency-check label: a design shaped by numbers that include the hold-out has seen it, whenever its
    first run was."""
    band = float(gate.cfg["holdout_check"]["band"])
    per = gate.stats_kw["periods"]
    r, h = mtm.dated(research, "research"), mtm.dated(holdout, "hold-out")
    lo, hi = gate.window
    if (h.index <= hi).any() or (r.index > hi).any() or h.size < 20:
        raise ValueError("the research series must end by the research window's last session and the hold-out "
                         "series (at least 20 sessions) must start after it")
    rows = bootstrap_paths(gate, r.size, h.size)
    frozen = pd.Timestamp(str(gate.cfg["holdout_check"]["contaminated_before"]))
    late_enough = first_run_at is not None and pd.Timestamp(_utc(first_run_at).date()) >= frozen
    saw = prereg.design_saw_holdout if prereg is not None and design_saw_holdout is None else design_saw_holdout
    clean = bool(late_enough and saw is False)
    if not late_enough:
        label = "consistency check: the rule was first run before the hold-out was frozen"
    elif saw is True:
        label = "consistency check: the design saw the hold-out (design_saw_holdout: true)"
    elif saw is None:
        label = "consistency check: the pre-registration does not declare design_saw_holdout: false"
    else:
        label = "out-of-sample check"
    out: dict = {"sessions": int(h.size), "band": band, "clean": clean, "label": label,
                 "design_saw_holdout": saw, "checks": {}}
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
           f"**Verdict: {rep.get('verdict_text') or rep['verdict']}**" + (" (cannot pass)" if not rep["can_pass"]
                                                                            else ""), ""]
    if rep.get("hindsight"):
        out += ["Hindsight (decision 18): these amendments came after the first logged run, so they may have been "
                "shaped by its results:", ""]
        out += [f"- {a['amendment']}: {a['why']}" for a in rep["hindsight"]] + [""]
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
                f"- Share of resampled decades that meet B2's branches: "
                f"{_pct(n['bootstrap_share_meeting']['either'], 0)}; "
                f"with a shallower drawdown than SPY: {_pct(n['bootstrap_share_shallower_drawdown'], 0)}."]
    ch, nb = n.get("chance") or {}, rep.get("book_configurations") or {}
    if ch:
        out.append(f"- Chance test (gate v3.1): Sharpe {ch['sharpe_book']:.2f} against SPY's {ch['sharpe_spy']:.2f}"
                   + (f", deflated {ch['dsr']:.3f} (hurdle {ch['hurdle']:.2f})" if ch.get("dsr") is not None else "")
                   + f"; N = {nb.get('configurations') if nb.get('configurations') is not None else 'unknown'} "
                     f"configurations of the idea `{nb.get('idea')}` ({', '.join(nb.get('books') or [])}).")
    out += ["", f"gate_v3.yaml sha256 `{rep['gate_sha']}`. Generated {rep['generated_at']}."]
    return "\n".join(out) + "\n"
