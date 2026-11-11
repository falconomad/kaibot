"""Replay the real Room over an evaluation set and score it.

The room may only veto a rules-approved candidate or halve its size. `contract_violations` checks every outcome
against that, independently of the room's own checks; the count must be zero.

Agreement with the outcome label (of the full-size rules-only trade): blocking a loser or allowing a winner
agrees (1), allowing a loser or blocking a winner disagrees (0), a downgrade counts 0.5 either way. Blocked means
no trade: a veto, or a session that failed closed (timeout, invalid, error).
`lift_r` is the R the room saved (+) or gave up (-) against rules-only: sum of (multiplier - 1) * r_multiple.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from kaibot.agents.evalset.cases import EvalCase
from kaibot.agents.evalset.stub import StubModel
from kaibot.agents.gateway import Gateway
from kaibot.agents.room import FORBIDDEN_FIELDS, Room, RoomOutcome

MULTIPLIER = {"allow": 1.0, "downgrade": 0.5, "veto": 0.0}
STATUSES = {"done", "timeout", "invalid", "error"}
NEVER_TRADED = {"company_news", "unknown"}


class OfflineBudget:
    """In-memory stand-in for the Postgres budget RPCs: it records the stub's estimated spend and never refuses,
    so an offline run cannot touch or be limited by the real budget."""

    def __init__(self):
        self.spent, self.open, self.ids = 0.0, {}, itertools.count(1)

    def rpc(self, fn: str, args: dict):
        if fn == "llm_reserve":
            cid = next(self.ids)
            self.open[cid] = float(args["p_reserved"])
            return cid
        if fn == "llm_settle":
            if self.open.pop(args["p_id"], None) is not None:
                self.spent += float(args["p_actual"])
            return None
        if fn == "llm_budget_state":
            return {"day": self.spent, "month": self.spent, "state": "ok", "alert": False}
        raise KeyError(fn)


def offline_gateway(client=None) -> Gateway:
    return Gateway(OfflineBudget(), client or StubModel())


@dataclass
class Result:
    case: EvalCase
    outcome: RoomOutcome
    violations: list[str] = field(default_factory=list)


def _keys(obj) -> set[str]:
    out, stack = set(), [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            out |= {str(k) for k in x}
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return out


def contract_violations(case: EvalCase, out) -> list[str]:
    """Every way `out` (a RoomOutcome) breaks "the room may only veto or halve". Empty list = clean."""
    bad = []
    mult, verdict, judge = out.trade_multiplier, out.verdict, out.judge or {}
    if out.status not in STATUSES:
        bad.append(f"unknown status {out.status!r}")
    if mult not in (0.0, 0.5, 1.0):
        bad.append(f"size multiplier {mult} is not 0, 0.5 or 1")
    if out.status != "done":
        if mult != 0.0 or verdict is not None:
            bad.append(f"status {out.status} must fail closed (multiplier {mult}, verdict {verdict!r})")
    elif verdict not in MULTIPLIER:
        bad.append(f"verdict {verdict!r} is not allow, downgrade or veto")
    else:
        if mult != MULTIPLIER[verdict]:
            bad.append(f"verdict {verdict} with multiplier {mult}")
        if mult > 0 and judge.get("cause") in NEVER_TRADED:
            bad.append(f"traded a {judge.get('cause')} drop")
        if judge.get("verdict") == "veto" and mult > 0:
            bad.append("the judge's veto was overridden")
        if judge.get("candidate_id") != case.case_id:
            bad.append("verdict for another candidate")
        ids = {str(it.get("id")) for it in case.evidence.get("items") or []}
        cited = list(judge.get("evidence_ids") or [])
        for part in (out.proposer or {}, out.critic or {}):
            cited += [e.get("id") for e in part.get("evidence") or []]
        unknown = sorted({str(c) for c in cited} - ids)
        if unknown:
            bad.append(f"cites evidence not in the pack {unknown[:3]}")
    leaked = sorted(_keys([out.proposer, out.critic, out.judge]) & FORBIDDEN_FIELDS)
    if leaked:
        bad.append(f"output carries price/size fields {leaked}")
    return bad


def replay(cases: list[EvalCase], gateway: Gateway | None = None, max_usd: float | None = None,
           room: Room | None = None) -> tuple[list[Result], list[EvalCase]]:
    """(results, not_run). Runs each case through Room.deliberate exactly as the engine would. With `max_usd`
    (live runs) it stops before a case once that much is spent or the shared budget is no longer "ok", so a live
    evaluation can never eat the engine's day."""
    gw = gateway or offline_gateway()
    room = room or Room(gw)
    results, spent = [], 0.0
    for i, c in enumerate(cases):
        if max_usd is not None and (spent >= max_usd or (gw.state() or {}).get("state") != "ok"):
            return results, cases[i:]
        out = room.deliberate(c.case_id, c.candidate(), c.evidence)
        spent += out.cost_usd
        results.append(Result(c, out, contract_violations(c, out)))
    return results, []


def report(results: list[Result], cost_source: str = "stub estimate") -> dict:
    n = len(results)

    def rate(k: int, d: int = n):
        return round(k / d, 4) if d else None

    def score(rs: list[Result]) -> dict:
        agree, lift, exp_ok, exp_n = 0.0, 0.0, 0, 0
        for r in rs:
            m, win = r.outcome.trade_multiplier, r.case.outcome.label == "win"
            agree += 0.5 if m == 0.5 else float((m == 1.0) == win)
            if r.case.outcome.r_multiple is not None:
                lift += (m - 1.0) * r.case.outcome.r_multiple
            if r.case.expected:
                exp_n += 1
                exp_ok += (r.outcome.verdict or "veto") == r.case.expected.verdict and r.outcome.status == "done"
        return {"n": len(rs), "veto": sum(r.outcome.verdict == "veto" for r in rs),
                "downgrade": sum(r.outcome.verdict == "downgrade" for r in rs),
                "allow": sum(r.outcome.verdict == "allow" for r in rs),
                "failed_closed": sum(r.outcome.status != "done" for r in rs),
                "agreement": rate(agree, len(rs)), "lift_r": round(lift, 3),
                "expected_match": rate(exp_ok, exp_n), "expected_n": exp_n}

    total = score(results)
    cost = sum(r.outcome.cost_usd for r in results)
    cats = sorted({r.case.category for r in results})
    return {
        "cases": n, "synthetic": sum(r.case.synthetic for r in results),
        "veto_rate": rate(total["veto"]), "downgrade_rate": rate(total["downgrade"]),
        "allow_rate": rate(total["allow"]), "failed_closed_rate": rate(total["failed_closed"]),
        "agreement_with_outcomes": total["agreement"], "lift_r": total["lift_r"],
        "expected_verdict_match": total["expected_match"], "expected_n": total["expected_n"],
        "contract_violations": [{"case_id": r.case.case_id, "violation": v} for r in results for v in r.violations],
        "cost_usd": round(cost, 5), "cost_per_decision_usd": round(cost / n, 5) if n else None,
        "cost_source": cost_source,
        "by_category": {c: score([r for r in results if r.case.category == c]) for c in cats},
        "decisions": [{"case_id": r.case.case_id, "category": r.case.category, "status": r.outcome.status,
                       "verdict": r.outcome.verdict, "multiplier": r.outcome.trade_multiplier,
                       "cause": (r.outcome.judge or {}).get("cause"), "outcome": r.case.outcome.label,
                       "expected": r.case.expected.verdict if r.case.expected else None,
                       "cost_usd": round(r.outcome.cost_usd, 5), "error": r.outcome.error} for r in results],
    }


def format_report(rep: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x:.0%}"

    v = rep["contract_violations"]
    lines = [
        f"cases                   {rep['cases']} ({rep['synthetic']} synthetic)",
        f"veto rate               {pct(rep['veto_rate'])}",
        f"downgrade rate          {pct(rep['downgrade_rate'])}",
        f"allow rate              {pct(rep['allow_rate'])}",
        f"failed closed           {pct(rep['failed_closed_rate'])}  (timeout, invalid or error: no trade)",
        f"agreement with outcomes {pct(rep['agreement_with_outcomes'])}  (lift vs rules-only {rep['lift_r']:+.2f} R)",
        f"expected verdict match  {pct(rep['expected_verdict_match'])}  ({rep['expected_n']} cases state one)",
        f"contract violations     {len(v)}  (must be 0: the room may only veto or halve)",
        f"cost per decision       ${rep['cost_per_decision_usd'] or 0:.4f}  (total ${rep['cost_usd']:.4f}, "
        f"{rep['cost_source']})",
        "", f"{'category':<18}{'n':>3}{'veto':>6}{'down':>6}{'allow':>6}{'failed':>7}{'agree':>7}",
    ]
    for c, s in rep["by_category"].items():
        lines.append(f"{c:<18}{s['n']:>3}{s['veto']:>6}{s['downgrade']:>6}{s['allow']:>6}{s['failed_closed']:>7}"
                     f"{pct(s['agreement']):>7}")
    lines += [f"VIOLATION {x['case_id']}: {x['violation']}" for x in v]
    return "\n".join(lines)
