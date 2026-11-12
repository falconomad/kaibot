"""Evidence gate v2 for multi-day rules (decision 15 section 7): the Gate B evaluator, the Gate C tracking report
and the portfolio view. Reports only: nothing here is imported by the engine and nothing here can trade.

The pass marks are pre-declared in `config/gate_v2.yaml`. `GateV2.load` hashes that file and every report made
here carries the hash as `gate_sha` (the mechanism of the M4 gate, `gate_report.Bar`). The M4 gate
(`config/gate.yaml`, `gate_report.py`) stays in force for intraday rules.

Fail closed. A mark that cannot be computed (no cost series, no random control, no point-in-time run) is `None`,
and `None` never passes. A run whose source is the research harness, cost model v1, today's stock list or
price-only SPY is `provisional`: it can fail, it cannot pass. A rule whose pre-registration is retroactive
cannot pass either.

Reading of the marks where the decision leaves room (the strict one each time; listed for Product in
docs/ops/gate_v2.md):
- mark 1 "6 of 10 calendar years": a first or last part-year counts as a year; the test must span at least 10
  calendar years; with more than 10, at least 60% of them (rounded up) must be positive; a year of exactly zero
  is not positive.
- mark 2: CAGR and drawdown of the rule and of SPY over the same sessions, drawdown marked to market.
- mark 3: the control must have filled every slot (`matched`), else the mark is unknown. When the rule's own
  names were also run through the control's simulator, the lower of the two percentiles is used.
- mark 4: marks 1 and 2 recomputed on net - cost (the cost charged a second time) against the same SPY.
- mark 5: marks 1 to 4 must hold on a run on the point-in-time universe (the run itself, or `pit_run`).
- mark 6: every neighbour named in the pre-registration must be supplied and pass mark 1; a rule with no
  neighbour has had no plateau check, so the mark is not met.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from kaibot.evaluation import mtm
from kaibot.evaluation.prereg import Prereg
from kaibot.evaluation.trials import Registry

ROOT = Path(__file__).resolve().parents[3]
DAYS_PER_MONTH = 30.44  # as in the M4 gate
REQUIRED = {  # section -> keys that must be present (a typo in the pre-registered file must fail loudly)
    "gate_a": ("max_neighbours",),
    "gate_b": ("out_of_sample_from", "mark_1", "mark_2", "mark_3", "mark_4", "mark_5", "mark_6", "final_requires"),
    "gate_c": ("min_months", "min_sessions", "drawdown_band", "retirement", "tracking", "bootstrap"),
    "statistics": ("periods_per_year", "sharpe_haircut", "sharpe_warning_above"),
}


# ---------------------------------------------------------------- pre-registered marks


@dataclass(frozen=True)
class GateV2:
    cfg: dict
    sha: str

    @classmethod
    def load(cls, root: Path | str | None = None) -> GateV2:
        raw = (Path(root or ROOT) / "config" / "gate_v2.yaml").read_bytes()
        return cls.from_bytes(raw)

    @classmethod
    def from_bytes(cls, raw: bytes) -> GateV2:
        cfg = yaml.safe_load(raw)
        for section, keys in REQUIRED.items():
            missing = [k for k in keys if k not in (cfg or {}).get(section, {})]
            if missing:
                raise ValueError(f"config/gate_v2.yaml: {section} lacks {', '.join(missing)}")
        return cls(cfg, hashlib.sha256(raw).hexdigest())

    @property
    def stats_kw(self) -> dict:
        s = self.cfg["statistics"]
        return {"periods": int(s["periods_per_year"]), "haircut": float(s["sharpe_haircut"]),
                "warn_above": float(s["sharpe_warning_above"])}


@dataclass(frozen=True)
class Run:
    """One simulated account, as Gate B reads it."""

    net: pd.Series  # daily mark-to-market returns after costs, indexed by session
    spy: pd.Series  # SPY total return on (at least) the same sessions
    costs: pd.Series | None = None  # each session's costs at 1x, as a share of that session's starting equity
    random: Any = None  # the matched random-entry control: a `ControlResult` or its dict
    universe: str = "today"  # "point_in_time" | "today"
    traded: pd.Series | None = None  # (bought + sold) / starting equity per session, for turnover
    exposure: pd.Series | None = None  # share of equity invested at each close


def _mark(met: bool | None, numbers: dict, limit: dict, why: str = "") -> dict:
    return {"met": met, "numbers": numbers, "limit": limit, "why": why}


def _window(gate: GateV2, run: Run) -> tuple[pd.Series, pd.Series, pd.Series | None, int]:
    """The run cut to the out-of-sample window, with SPY and costs on the same sessions."""
    start = pd.Timestamp(gate.cfg["gate_b"]["out_of_sample_from"])
    net = mtm.dated(run.net, "strategy returns")
    keep = net[net.index >= start]
    if keep.size < 3:
        raise ValueError(f"fewer than 3 sessions on or after {start.date()}: nothing to judge")
    keep, spy = mtm.align(keep, run.spy)
    costs = None
    if run.costs is not None:
        costs = mtm.align(keep, mtm.dated(run.costs, "costs"))[1]
    return keep, spy, costs, int(net.size - keep.size)


# ---------------------------------------------------------------- the six marks


def mark_1(gate: GateV2, net: pd.Series) -> dict:
    """Net return positive overall and in at least 6 of 10 calendar years."""
    m = gate.cfg["gate_b"]["mark_1"]
    years = mtm.calendar_years(net)
    n, pos = len(years), sum(y["return"] > 0.0 for y in years)
    need = max(int(m["min_positive_years"]), math.ceil(m["min_positive_years"] * n / m["of_years"] - 1e-9))
    total = mtm.total_return(net)
    why = []
    if not total > 0.0:
        why.append("net return is not positive")
    if n < m["of_years"]:
        why.append(f"the test spans {n} calendar years, the mark needs {m['of_years']}")
    if pos < need:
        why.append(f"{pos} positive years, {need} needed")
    return _mark(not why, {"total_return": total, "positive_years": pos, "years": n,
                           "partial_years": sum(y["partial"] for y in years)},
                 {"total_return_above": 0.0, "positive_years_min": need, "years_min": int(m["of_years"])},
                 "; ".join(why))


def mark_2(gate: GateV2, net: pd.Series, spy: pd.Series) -> dict:
    """EITHER CAGR >= SPY - 2 points with drawdown <= 0.75 x SPY's, OR CAGR >= SPY + 1 point with <= 1.1 x."""
    m = gate.cfg["gate_b"]["mark_2"]
    periods = gate.stats_kw["periods"]
    c, cs = mtm.cagr(net, periods), mtm.cagr(spy, periods)
    dd, dds = mtm.max_drawdown(net), mtm.max_drawdown(spy)
    points = (c - cs) * 100.0
    branch = {k: bool(points >= b["cagr_vs_spy_points_min"] and dd <= b["max_drawdown_vs_spy_max"] * dds)
              for k, b in m.items()}
    met = any(branch.values())
    return _mark(met, {"cagr": c, "spy_cagr": cs, "cagr_vs_spy_points": points, "max_drawdown": dd,
                       "spy_max_drawdown": dds, "max_drawdown_vs_spy": dd / dds if dds > 0 else math.inf,
                       "branch": branch},
                 {k: dict(b) for k, b in m.items()},
                 "" if met else "neither branch holds")


def mark_3(gate: GateV2, random: Any) -> dict:
    """Net Sharpe at or above the 90th percentile of matched random entries (at least 500 runs)."""
    m = gate.cfg["gate_b"]["mark_3"]
    limit = {"percentile_min": m["random_percentile_min"], "runs_min": m["random_runs_min"]}
    if random is None:
        return _mark(None, {}, limit, "no matched random-entry control was supplied")
    r = random.to_dict() if hasattr(random, "to_dict") else dict(random)
    numbers = {k: r.get(k) for k in ("percentile", "percentile_simulated", "runs", "strategy_sharpe",
                                     "simulated_strategy_sharpe", "random_median", "random_p90", "matched",
                                     "unfilled")}
    if int(r.get("runs") or 0) < m["random_runs_min"]:
        return _mark(None, numbers, limit, f"{r.get('runs')} runs, {m['random_runs_min']} needed")
    if not r.get("matched", False):
        return _mark(None, numbers, limit, "the control did not fill every slot, so it is not matched")
    used = float(r["percentile"])
    if r.get("percentile_simulated") is not None:  # the rule's own names through the control's simulator
        used = min(used, float(r["percentile_simulated"]))
    numbers["percentile_used"] = used
    met = used >= m["random_percentile_min"]
    return _mark(met, numbers, limit, "" if met else "below the required percentile")


def mark_4(gate: GateV2, net: pd.Series, spy: pd.Series, costs: pd.Series | None) -> dict:
    """Marks 1 and 2 still hold at 2x costs."""
    k = float(gate.cfg["gate_b"]["mark_4"]["costs_multiple"])
    limit = {"costs_multiple": k}
    if costs is None:
        return _mark(None, {}, limit, "no daily cost series was supplied")
    stressed = pd.Series(mtm.at_cost_multiple(net, costs, k), index=net.index)
    m1, m2 = mark_1(gate, stressed), mark_2(gate, stressed, spy)
    met = bool(m1["met"] and m2["met"])
    return _mark(met, {"mark_1": m1["met"], "mark_2": m2["met"], "total_return": m1["numbers"]["total_return"],
                       "positive_years": m1["numbers"]["positive_years"], "cagr": m2["numbers"]["cagr"],
                       "max_drawdown": m2["numbers"]["max_drawdown"],
                       "breakeven_multiple": mtm.breakeven_cost_multiple(net, costs)},
                 limit, "" if met else "; ".join(x for x in (m1["why"], m2["why"]) if x))


def _marks_1_to_4(gate: GateV2, run: Run) -> dict:
    net, spy, costs, _ = _window(gate, run)
    return {"mark_1": mark_1(gate, net), "mark_2": mark_2(gate, net, spy), "mark_3": mark_3(gate, run.random),
            "mark_4": mark_4(gate, net, spy, costs)}


def mark_5(gate: GateV2, run: Run, own: dict, pit_run: Run | None) -> dict:
    """Holds on the point-in-time universe, not only on today's list."""
    want = gate.cfg["gate_b"]["mark_5"]["universe"]
    limit = {"universe": want, "marks": [1, 2, 3, 4]}
    if run.universe == want:
        marks, where = own, "this run is on the point-in-time universe"
    elif pit_run is not None and pit_run.universe == want:
        marks, where = _marks_1_to_4(gate, pit_run), "a second run on the point-in-time universe"
    else:
        return _mark(None, {"universe": run.universe}, limit, "no run on the point-in-time universe was supplied")
    got = {k: v["met"] for k, v in marks.items()}
    met = None if any(v is None for v in got.values()) and all(v is not False for v in got.values()) else \
        all(v is True for v in got.values())
    return _mark(met, {"universe": want, "source": where, **got}, limit,
                 "" if met else "marks 1 to 4 do not all hold on the point-in-time universe")


def mark_6(gate: GateV2, neighbours: Mapping[str, pd.Series] | None, declared: tuple[str, ...] | None) -> dict:
    """Every pre-declared neighbour passes mark 1."""
    limit = {"every_declared_neighbour_passes_mark": 1, "neighbours_max": gate.cfg["gate_a"]["max_neighbours"]}
    neighbours = dict(neighbours or {})
    if declared is None:
        return _mark(None, {"supplied": sorted(neighbours)}, limit,
                     "no pre-registration, so no neighbours were declared")
    if not declared:
        return _mark(False, {"declared": []}, limit, "the pre-registration declares no neighbour: no plateau check")
    start = pd.Timestamp(gate.cfg["gate_b"]["out_of_sample_from"])
    results, why = {}, []
    for name in declared:
        if name not in neighbours:
            results[name] = None
            why.append(f"{name}: no result supplied")
            continue
        s = mtm.dated(neighbours[name], name)
        m1 = mark_1(gate, s[s.index >= start])
        results[name] = m1["met"]
        if not m1["met"]:
            why.append(f"{name}: {m1['why']}")
    extra = sorted(set(neighbours) - set(declared))
    if extra:
        why.append(f"not pre-declared, ignored: {', '.join(extra)}")
    met = None if any(v is None for v in results.values()) and all(v is not False for v in results.values()) \
        else all(v is True for v in results.values())
    return _mark(met, {"declared": list(declared), "results": results, "undeclared": extra}, limit, "; ".join(why))


# ---------------------------------------------------------------- statistics block


def statistics(gate: GateV2, net: pd.Series, spy: pd.Series, *, costs: pd.Series | None = None,
               registry: Registry | None = None, rule: str | None = None, traded: pd.Series | None = None,
               exposure: pd.Series | None = None) -> dict:
    """The numbers every gate v2 report shows for one daily series against SPY total return: return, CAGR,
    mark-to-market drawdown, Sharpe with Newey-West error and haircut, alpha and beta, calendar years, cost
    multiples, turnover and the deflated Sharpe against the trial registry."""
    kw = gate.stats_kw
    net, spy = mtm.align(net, spy)
    out = {"window": [net.index[0].date().isoformat(), net.index[-1].date().isoformat()],
           "rule": mtm.curve(net, **kw), "spy": mtm.curve(spy, **kw),
           "alpha_beta": asdict(mtm.alpha_beta(net, spy, periods=kw["periods"])),
           "calendar_years": mtm.calendar_years(net, spy),
           "turnover": mtm.turnover(mtm.align(net, traded)[1], kw["periods"]) if traded is not None else None,
           "costs": None, "exposure_matched_spy": None, "deflated_sharpe": None}
    if costs is not None:
        multiples = gate.cfg["gate_b"]["mark_4"]["reported_multiples"]
        out["costs"] = mtm.cost_table(net, mtm.align(net, costs)[1], multiples, **kw)
    if exposure is not None:
        e = mtm.align(net, exposure)[1]
        out["exposure_matched_spy"] = {"mean_exposure": float(e.mean()),
                                       **mtm.curve(mtm.exposure_matched(spy, e), **kw)}
    if registry is not None:
        sharpes = registry.daily_sharpes().get(rule or "", [])
        var = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else None
        out["deflated_sharpe"] = {**asdict(mtm.deflated_sharpe(net, registry.configurations, var,
                                                                periods=kw["periods"])),
                                  "trial_sharpes_used": len(sharpes) if var is not None else 0,
                                  "registry": registry.summary()}
    return out


# ---------------------------------------------------------------- Gate B


def evaluate_gate_b(gate: GateV2, *, rule: str, run: Run, pit_run: Run | None = None,
                    neighbours: Mapping[str, pd.Series] | None = None, prereg: Prereg | None = None,
                    source: Mapping[str, Any] | None = None, registry: Registry | None = None,
                    first_run_at: datetime | None = None, as_of: datetime | None = None) -> dict:
    """Gate B for one rule: each mark with pass / fail / unknown and its numbers, as JSON-ready data.

    run: the account under test. pit_run: the same rule on the point-in-time universe when `run` is on today's
    list. neighbours: daily net returns of each pre-declared neighbour. prereg: `prereg.load(rule)`.
    source: where the run came from, e.g. {"engine": "research_harness", "cost_model": "v1", "data_repairs":
    False, "benchmark": "spy_price_only"}; compared with `gate_b.final_requires`. registry: the trial registry
    (deflated Sharpe; and the `prereg_sha256` its rows carry for this rule must be the hash of `prereg`).
    first_run_at: when the rule was first run (earliest trial row), for Gate A.

    verdict: "fail" (a mark is false), "incomplete" (a mark could not be computed), "provisional" (all marks
    hold but the run or the pre-registration cannot carry a pass), "pass"."""
    net, spy, costs, dropped = _window(gate, run)
    marks = _marks_1_to_4(gate, run)
    marks["mark_5"] = mark_5(gate, run, marks, pit_run)
    marks["mark_6"] = mark_6(gate, neighbours, prereg.neighbours if prereg is not None else None)

    need = gate.cfg["gate_b"]["final_requires"]
    src = dict(source or {})
    reasons = [f"{k} is {src.get(k, 'not stated')!r}, a pass needs {v!r}" for k, v in need.items() if src.get(k) != v]
    before = None
    if prereg is not None and prereg.committed_at is not None and first_run_at is not None:
        before = _utc(prereg.committed_at) <= _utc(first_run_at)
    logged = {r.get("prereg_sha256") for r in (registry.rows if registry is not None else ())
              if r.get("strategy") == rule and r.get("prereg_sha256")}
    unchanged = None if prereg is None or not logged else logged == {prereg.sha256}
    checks = {"preregistered": prereg is not None,
              "not_retroactive": None if prereg is None else not prereg.retroactive,
              "neighbours_at_most": None if prereg is None else
              len(prereg.neighbours) <= gate.cfg["gate_a"]["max_neighbours"],
              "committed_before_first_run": before,
              "unchanged_since_logged_runs": unchanged}  # None: no run of this rule logged a hash
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
            reasons.append("a run of this rule was logged with a different pre-registration hash: the file "
                           "changed after a run (Gate A)")

    met = [m["met"] for m in marks.values()]
    if any(v is False for v in met):
        verdict = "fail"
    elif any(v is None for v in met):
        verdict = "incomplete"
    else:
        verdict = "provisional" if reasons else "pass"
    return _clean({
        "gate": "B", "rule": rule, "generated_at": _iso(as_of or datetime.now(UTC)), "gate_sha": gate.sha,
        "prereg": prereg.summary() if prereg is not None else None, "gate_a": checks, "source": src,
        "sessions": int(net.size), "sessions_before_window_dropped": dropped,
        "marks": marks, "provisional": bool(reasons), "provisional_reasons": reasons, "verdict": verdict,
        "can_pass": not reasons,
        "statistics": statistics(gate, net, spy, costs=costs, registry=registry, rule=rule, traded=run.traded,
                                 exposure=run.exposure),
    })


# ---------------------------------------------------------------- Gate C


def gate_c_tracking(gate: GateV2, *, rule: str, live: pd.Series, replay: pd.Series, test_returns,
                    reconciled: Mapping | pd.Series | None = None, trigger_committed_at: datetime | None = None,
                    frozen_since: datetime | date | None = None, as_of: datetime | None = None) -> dict:
    """Gate C tracking report: the live paper record against the simulated replay of the same days.

    live: daily mark-to-market returns of the paper account for this rule, one per session since the start.
    replay: what the `SimBroker` replay made on those same sessions. test_returns: the daily returns of the
    Gate B test; the drawdown bands are resampled from them for a stretch as long as the live record.
    reconciled: per session, True when the ledger equalled the broker that day. trigger_committed_at: when the
    retirement trigger (this config, or the rule's incubation plan) was committed. frozen_since: the start of
    the rule's current code (`gate_report.frozen_since(starts, fingerprint, rule)`); only sessions that ran
    wholly on that code count, so a change to the rule or to the shared core restarts the incubation.

    Marks: duration (months and sessions), reconciliation every day, tracking inside the tolerance, live
    drawdown inside the 95% band, trigger written before the start. Retirement triggers: live drawdown outside
    the 99% band, or tracking outside the tolerance on the pre-declared number of sessions.
    status: "retire" (a trigger fired), "fail" (a mark other than the duration is false), "incomplete" (such a
    mark could not be computed; it counts as not met), "incubating" (only the duration is still short), "pass"."""
    c = gate.cfg["gate_c"]
    kw = gate.stats_kw
    live = mtm.dated(live, "live returns")
    rep = mtm.dated(replay, "replay returns")
    older = 0
    if frozen_since is not None:
        first = _first_full_session(frozen_since)
        older = int((live.index < first).sum())
        live, rep = live[live.index >= first], rep[rep.index >= first]
    missing = live.index.difference(rep.index)
    both = live.index.intersection(rep.index)
    diff_bp = (live.reindex(both) - rep.reindex(both)) * 1e4

    sessions = int(live.size)
    months = float((live.index[-1] - live.index[0]).days / DAYS_PER_MONTH) if sessions else 0.0
    duration = _mark(sessions >= c["min_sessions"] and months >= c["min_months"],
                     {"sessions": sessions, "months": months, "sessions_on_older_code_excluded": older},
                     {"sessions_min": c["min_sessions"], "months_min": c["min_months"]})

    rec = pd.Series(reconciled, dtype=object) if reconciled is not None else None
    if rec is None:
        recon = _mark(None, {}, {"every_day": True}, "no reconciliation record was supplied")
    else:
        rec.index = pd.DatetimeIndex(pd.to_datetime(rec.index)).normalize()
        got = rec.reindex(live.index)
        unknown = [d.date().isoformat() for d in got.index[got.isna()]]
        bad = [d.date().isoformat() for d in got.index[got.notna() & (got != True)]]  # noqa: E712
        recon = _mark(False if bad else (None if unknown else True),
                      {"days_not_equal": bad, "days_without_record": unknown}, {"every_day": True},
                      "ledger and broker differed" if bad else ("days without a record" if unknown else ""))

    tol = c["tracking"]["tolerance_bp_per_session"]
    need = int(c["retirement"]["tracking_breach_sessions"])
    numbers = {"sessions_compared": int(both.size),
               "sessions_without_replay": [d.date().isoformat() for d in missing],
               "mean_diff_bp": float(diff_bp.mean()) if both.size else None,
               "max_abs_diff_bp": float(diff_bp.abs().max()) if both.size else None,
               "cumulative_diff_bp": float(diff_bp.sum()) if both.size else None,
               "tracking_error_ann_bp": float(diff_bp.std(ddof=1) * math.sqrt(kw["periods"])) if both.size > 1
               else None,
               "correlation": float(np.corrcoef(live.reindex(both), rep.reindex(both))[0, 1])
               if both.size > 2 and live.reindex(both).std() > 0 and rep.reindex(both).std() > 0 else None}
    limit = {"tolerance_bp_per_session": tol, "breach_sessions": need, "breach_rule": c["tracking"]["breach_rule"]}
    breaches = None
    if tol is None:
        tracking = _mark(None, numbers, limit, "the tracking tolerance is not set in config/gate_v2.yaml")
    else:
        out = diff_bp.abs() > float(tol)
        breaches = _longest_run(out.to_numpy()) if c["tracking"]["breach_rule"] == "consecutive" else int(out.sum())
        numbers.update(sessions_outside_tolerance=int(out.sum()), breach_count=breaches,
                       breach_days=[d.date().isoformat() for d in out.index[out]])
        if breaches >= need:
            tracking = _mark(False, numbers, limit, f"outside the tolerance on {breaches} sessions")
        elif len(missing):
            tracking = _mark(None, numbers, limit, "some live sessions have no replay")
        else:
            tracking = _mark(True, numbers, limit)

    b = c["bootstrap"]
    bands = mtm.drawdown_bands(test_returns, horizon=max(sessions, 1), runs=int(b["runs"]),
                               mean_block=float(b["mean_block_sessions"]), seed=int(b["seed"]))
    live_dd = mtm.max_drawdown(live)
    drawdown = _mark(bool(live_dd <= bands.q95), {"live_max_drawdown": live_dd, "band_95": bands.q95,
                                                   "band_99": bands.q99, "band_median": bands.median},
                     {"inside_band": c["drawdown_band"], "bootstrap": asdict(bands)},
                     "" if live_dd <= bands.q95 else "live drawdown is outside the 95% band of the test")

    written = None if trigger_committed_at is None or not sessions else \
        bool(_utc(trigger_committed_at).date() <= live.index[0].date())
    trigger = _mark(written, {"trigger_committed_at": _iso(trigger_committed_at) if trigger_committed_at else None,
                              "first_session": live.index[0].date().isoformat() if sessions else None},
                    {"written_before_start": True},
                    "" if written else "the retirement trigger is not shown to be committed before the start")

    retire = []
    if live_dd > bands.q99:
        retire.append(f"live drawdown {live_dd:.2%} is outside the 99% band ({bands.q99:.2%})")
    if breaches is not None and breaches >= need:
        retire.append(f"tracking outside the tolerance of {tol} bp on {breaches} sessions (trigger: {need})")
    marks = {"duration": duration, "reconciliation": recon, "tracking": tracking, "drawdown": drawdown,
             "trigger_written_before_start": trigger}
    others = [m["met"] for k, m in marks.items() if k != "duration"]
    if retire:
        status = "retire"
    elif any(v is False for v in others):
        status = "fail"
    elif any(v is None for v in others):
        status = "incomplete"
    else:
        status = "pass" if duration["met"] else "incubating"
    return _clean({
        "gate": "C", "rule": rule, "generated_at": _iso(as_of or datetime.now(UTC)), "gate_sha": gate.sha,
        "marks": marks, "retire": bool(retire), "retirement_reasons": retire, "status": status,
        "retirement_trigger": {"drawdown_outside_band": c["retirement"]["drawdown_band"],
                               "tracking_breach_sessions": need, "tolerance_bp_per_session": tol},
        "live": mtm.curve(live, **kw) if sessions >= 3 else None,
        "replay": mtm.curve(rep.reindex(both), **kw) if both.size >= 3 else None,
    })


def _first_full_session(since: datetime | date) -> pd.Timestamp:
    """The first day whose whole session ran on code frozen at `since`: the same New York day when the code was
    in place before the 09:30 open, otherwise the next day. A plain date means "from that day on"."""
    if not isinstance(since, datetime):
        return pd.Timestamp(since)
    t = pd.Timestamp(_utc(since)).tz_convert("America/New_York")
    day = t.normalize().tz_localize(None)
    return day if (t.hour, t.minute) < (9, 30) else day + pd.Timedelta(days=1)


def _longest_run(flags: np.ndarray) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


# ---------------------------------------------------------------- portfolio view (audit G4)


def portfolio_view(gate: GateV2, rules: Mapping[str, pd.Series], spy: pd.Series, *,
                   weights: Mapping[str, float] | None = None, as_of: datetime | None = None) -> dict:
    """The book across rules: each rule, the combined book, and how the rules move together once the market
    is taken out.

    rules: daily net mark-to-market returns of each rule's own account. weights: the share of the capital each
    rule runs (default: equal shares; a sum below 1 leaves the rest in cash). The combined book's daily return
    is the weighted sum, i.e. the shares are restored every day.

    Three correlation tables: `raw` returns; `excess` = rule - SPY; `residual` = rule - alpha - beta x SPY.
    Long-only rules all carry market exposure, so `raw` is high whatever the rules do, and `excess` is high
    too whenever the rules are less than fully invested (each is then short the same slice of SPY). `residual`
    is the one that says whether two rules are the same bet."""
    if not rules:
        raise ValueError("no rules to combine")
    series = {k: mtm.dated(v, k) for k, v in rules.items()}
    common = None
    for s in series.values():
        common = s.index if common is None else common.intersection(s.index)
    if common is None or len(common) < 10:
        raise ValueError("the rules share fewer than 10 sessions")
    frame = pd.DataFrame({k: s.reindex(common) for k, s in series.items()})
    bench = mtm.align(frame.iloc[:, 0], spy)[1]
    names = list(frame.columns)
    w = {k: 1.0 / len(names) for k in names} if weights is None else {k: float(weights.get(k, 0.0)) for k in names}
    if any(v < 0 for v in w.values()) or sum(w.values()) > 1.0 + 1e-9:
        raise ValueError("weights must be >= 0 and add up to at most 1 (no leverage)")
    kw = gate.stats_kw
    ab = {k: mtm.alpha_beta(frame[k], bench, periods=kw["periods"]) for k in names}
    excess = frame.sub(bench, axis=0)
    resid = pd.DataFrame({k: frame[k] - ab[k].alpha / kw["periods"] - ab[k].beta * bench for k in names})
    combined = sum(frame[k] * w[k] for k in names)
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    rc = resid.corr()
    vols = {k: float(frame[k].std(ddof=1)) for k in names}
    return _clean({
        "view": "portfolio", "generated_at": _iso(as_of or datetime.now(UTC)), "gate_sha": gate.sha,
        "window": [common[0].date().isoformat(), common[-1].date().isoformat()], "sessions": int(len(common)),
        "sessions_dropped": {k: int(series[k].size - len(common)) for k in names}, "weights": w,
        "rules": {k: {**mtm.curve(frame[k], **kw), "alpha_beta": asdict(ab[k])} for k in names},
        "combined": {**mtm.curve(combined, **kw),
                     "alpha_beta": asdict(mtm.alpha_beta(combined, bench, periods=kw["periods"])),
                     "vol_vs_weighted_sum_of_vols": float(combined.std(ddof=1) / sum(w[k] * vols[k] for k in names))
                     if sum(w[k] * vols[k] for k in names) > 0 else None},
        "spy": mtm.curve(bench, **kw),
        "correlation": {"raw": _matrix(frame.corr()), "excess": _matrix(excess.corr()), "residual": _matrix(rc)},
        "mean_pairwise_residual_correlation": float(np.mean([rc.loc[a, b] for a, b in pairs])) if pairs else None,
    })


def _matrix(df: pd.DataFrame) -> dict:
    return {a: {b: float(df.loc[a, b]) for b in df.columns} for a in df.index}


# ---------------------------------------------------------------- text


def _pct(x: float | None, digits: int = 2) -> str:
    return "n/a" if x is None or isinstance(x, str) else f"{x * 100:.{digits}f}%"


def _state(met: bool | None) -> str:
    return {True: "pass", False: "FAIL", None: "unknown (counts as not met)"}[met]


def _sharpe_cell(curve: dict, share: float) -> str:
    """A Sharpe is never printed without its haircut."""
    return mtm.sharpe_line(curve["sharpe"]["sharpe"], share)


def render_gate_b(rep: dict) -> str:
    """The Gate B report as Markdown. Every Sharpe carries its haircut on the same line."""
    st, m = rep["statistics"], rep["marks"]
    share = st["rule"]["sharpe"]["haircut_share"]
    labels = {"mark_1": "1. Net return positive overall and in enough calendar years",
              "mark_2": "2. CAGR and drawdown against SPY",
              "mark_3": "3. Net Sharpe against matched random entries",
              "mark_4": "4. Marks 1-2 at the stressed cost multiple",
              "mark_5": "5. Holds on the point-in-time universe",
              "mark_6": "6. Every pre-declared neighbour passes mark 1"}
    kind = (rep.get("source") or {}).get("benchmark")
    bench = {"spy_total_return": "SPY total return", "spy_price_only": "SPY, price only (no dividends)"}.get(
        kind, "SPY (kind of return not stated)")
    out = [f"# Gate B: {rep['rule']} ({st['window'][0]} to {st['window'][1]})", "",
           f"**Verdict: {rep['verdict']}**" + (" (cannot pass)" if not rep["can_pass"] else ""), ""]
    if rep["provisional_reasons"]:
        out += ["This run cannot pass, whatever the marks say:", ""]
        out += [f"- {r}" for r in rep["provisional_reasons"]] + [""]
    out += ["| Mark | Result | Why |", "|---|---|---|"]
    out += [f"| {labels[k]} | {_state(m[k]['met'])} | {m[k]['why'] or '-'} |" for k in labels]
    r, s, ab = st["rule"], st["spy"], st["alpha_beta"]
    sh = r["sharpe"]
    out += ["", f"| Daily mark-to-market, net | Rule | {bench} |", "|---|---|---|",
            f"| Total return | {_pct(r['total_return'])} | {_pct(s['total_return'])} |",
            f"| CAGR | {_pct(r['cagr'])} | {_pct(s['cagr'])} |",
            f"| Max drawdown | {_pct(r['max_drawdown'])} | {_pct(s['max_drawdown'])} |",
            f"| Sharpe | {_sharpe_cell(r, share)} | {_sharpe_cell(s, share)} |",
            f"| t-stat (Newey-West, {sh['lags']} lags) | {sh['t']:.2f} (plain t {sh['t_naive']:.2f}) | "
            f"{s['sharpe']['t']:.2f} |", "",
            f"- Alpha against {bench}: {_pct(ab['alpha'])} a year, t {ab['alpha_t']:.2f} (Newey-West, "
            f"{ab['lags']} lags); beta {ab['beta']:.2f}; R-squared {ab['r2']:.2f}."]
    if sh["warning"]:
        out.append(f"- WARNING: a backtested Sharpe of {_sharpe_cell(r, share)} is above the level where a "
                   "long-only stock rule is a warning sign, not a result.")
    d = st["deflated_sharpe"]
    if d:
        out.append(f"- Deflated Sharpe: probability {d['dsr']:.3f} that the true Sharpe is above the hurdle; Sharpe "
                   f"{mtm.sharpe_line(d['sharpe'], share)} against a hurdle of {d['hurdle']:.2f} for "
                   f"{d['n_trials']:,} configurations in the trial registry (V: {d['var_source']}).")
    else:
        out.append("- Deflated Sharpe: not computed (no trial registry was supplied).")
    if st["turnover"] is not None:
        out.append(f"- Turnover: {st['turnover']:.2f} times the book a year (bought plus sold).")
    e = st["exposure_matched_spy"]
    if e:
        out.append(f"- SPY held at the rule's own exposure (mean {_pct(e['mean_exposure'], 0)}): CAGR "
                   f"{_pct(e['cagr'])}, max drawdown {_pct(e['max_drawdown'])}, Sharpe {_sharpe_cell(e, share)}.")
    if st["costs"]:
        out += ["", "| Costs | Total return | CAGR | Max drawdown | Sharpe |", "|---|---|---|---|---|"]
        out += [f"| {k} | {_pct(v['total_return'])} | {_pct(v['cagr'])} | {_pct(v['max_drawdown'])} | "
                f"{_sharpe_cell(v, share)} |" for k, v in st["costs"].items() if k != "breakeven_multiple"]
        be = st["costs"]["breakeven_multiple"]
        out += ["", f"- Break-even cost multiple: {be if isinstance(be, str) else f'{be:.2f}'}x the modelled costs."]
    out += ["", "| Year | Sessions | Rule | SPY | Difference | Max drawdown |", "|---|---|---|---|---|---|"]
    out += [f"| {y['year']}{' (part)' if y['partial'] else ''} | {y['sessions']} | {_pct(y['return'])} | "
            f"{_pct(y.get('benchmark'))} | {_pct(y.get('excess'))} | {_pct(y['max_drawdown'])} |"
            for y in st["calendar_years"]]
    p = rep["prereg"]
    out += ["", f"gate_v2.yaml sha256 `{rep['gate_sha']}`; pre-registration "
            + (f"`{p['sha256']}` ({p['status']}, {p['date']})" if p else "none"),
            f"Generated {rep['generated_at']}."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- helpers


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _iso(t: datetime) -> str:
    return _utc(t).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clean(x: Any) -> Any:
    """JSON-ready: plain Python types, dates as text, NaN as null, infinities as "inf" / "-inf"."""
    if is_dataclass(x) and not isinstance(x, type):
        x = asdict(x)
    if isinstance(x, Mapping):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return [_clean(v) for v in x.tolist()]
    if isinstance(x, bool | np.bool_):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, float | np.floating):
        x = float(x)
        if math.isnan(x):
            return None
        return x if math.isfinite(x) else ("inf" if x > 0 else "-inf")
    if isinstance(x, datetime | date | pd.Timestamp):
        return x.isoformat()
    return x
