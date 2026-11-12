"""Pre-open review of a daily rule's candidate list (issue #131, M7·S91). Contracts-first slice: no LLM call, no
engine wiring, off by default.

Where it sits (once Backend-quant wires it): after the portfolio builder has turned the day's `TargetList`s into
whole-share targets for the next session, and before the orders are queued (09:00 ET on the daily path). The
review costs the daily rule no latency: the orders wait for the auction anyway.

What it may do, and nothing else:
- It reviews only increases: a (strategy, symbol) whose rules target is above the current position (a new buy or
  a top-up). Sales, trims and unchanged positions are never shown to it and never changed by it.
- Per candidate: allow (the rules' target), downgrade (half of the rules' increase, rounded down to whole shares)
  or veto (no increase: the target stays at the current position).
- It works on the builder's output, not on the target lists. Removing or halving a weight before the builder
  could let another name into the freed room (maximum names, pro-rata caps). Clamping the finished plan cannot:
  every reviewed target lies between the current position and the rules' target, so it only tightens.

Fail closed, as in the intraday room: a review that takes longer than `total_s`, errors, answers for another
session, misses a candidate, names an unknown or duplicate candidate, or fails the contract vetoes every increase of
that session. So does a review that is switched on with no reviewer wired (a configuration error, not "off").
Budget: at the daily soft cap (`rules_only`) or with an unknown budget state the review is skipped and the rules'
plan goes through unchanged (status "skipped"); at the monthly hard cap (`no_entries`) no increase goes through.
Off (the default) leaves the plan unchanged and calls nothing. Switching it on is the stakeholder's decision.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from pydantic import ValidationError

from kaibot.agents.contracts import PreopenVerdicts
from kaibot.strategies.base import TargetList

Key = tuple[str, str]  # (strategy, symbol), as in risk/portfolio.py
MULTIPLIER = {"allow": 1.0, "downgrade": 0.5, "veto": 0.0}
MAX_CANDIDATES = 50  # PreopenVerdicts.verdicts max_length; more growth keys than this fails closed
FLAG_ENV = "KAIBOT_PREOPEN_REVIEW"
ON = ("1", "on", "true", "yes")
STATUSES = ("off", "skipped", "done", "invalid", "timeout", "error", "no_entries")

# What the reviewer is shown about a candidate. Shares, prices and ATR stay out: the review judges events, not size.
TOTAL_S = 120.0  # the whole review's time limit; the daily orders wait for the auction, so this is generous

SHOWN_FEATURES = ("score", "rank", "ret_12_1", "earnings_in_days", "corporate_action", "news_7d", "sector_etf")

Reviewer = Callable[[date, list["ReviewCandidate"]], "PreopenVerdicts | dict | None"]


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """On only when the room is on (KAIBOT_ROOM) AND the pre-open review flag is on. Default: off."""
    env = os.environ if env is None else env
    return all((env.get(k) or "").strip().lower() in ON for k in ("KAIBOT_ROOM", FLAG_ENV))


@dataclass(frozen=True)
class ReviewCandidate:
    candidate_id: str  # "<strategy>:<symbol>"
    strategy: str
    symbol: str
    session: date
    kind: str  # "new" (no position today) | "add" (a top-up of a held position)
    rank: int | None  # 1 = the rule's best; None when the strategy sent no list
    reasons: tuple[str, ...] = ()
    features: dict = field(default_factory=dict)

    def shown(self) -> dict:
        """The reviewer's view of the candidate (JSON-safe, no shares or prices)."""
        return {"id": self.candidate_id, "strategy": self.strategy, "symbol": self.symbol,
                "session": self.session.isoformat(), "kind": self.kind, "rank": self.rank,
                "reasons": list(self.reasons), "features": dict(self.features)}


def candidate_id(key: Key) -> str:
    return f"{key[0]}:{key[1]}"


def _shown_features(t: TargetList | None, symbol: str) -> dict:
    per = (t.features or {}).get(symbol) if t is not None else None
    if not isinstance(per, Mapping):
        return {}
    return {k: per[k] for k in SHOWN_FEATURES if k in per and isinstance(per[k], str | int | float | bool | None)}


def candidates(session: date, targets: Iterable[TargetList], plan: Mapping[Key, int],
               current: Mapping[Key, float]) -> list[ReviewCandidate]:
    """Every (strategy, symbol) whose rules target grows, so nothing grows unreviewed. Ordered by strategy, then
    the rule's rank (weight high to low, then symbol)."""
    lists = {t.strategy: t for t in targets}
    out = []
    for key in sorted(plan):
        cur = float(current.get(key, 0) or 0)
        if not plan[key] > cur:
            continue
        strat, sym = key
        t = lists.get(strat)
        rank = None
        if t is not None and sym in t.weights:
            order = sorted(t.weights, key=lambda s: (-float(t.weights[s]), s))
            rank = order.index(sym) + 1
        out.append(ReviewCandidate(candidate_id(key), strat, sym, session, "add" if cur > 0 else "new", rank,
                                   tuple(t.reasons) if t is not None else (), _shown_features(t, sym)))
    out.sort(key=lambda c: (c.strategy, c.rank if c.rank is not None else 10**9, c.symbol))
    return out


@dataclass
class ReviewOutcome:
    status: str  # one of STATUSES
    session: date
    verdicts: dict[str, str] = field(default_factory=dict)  # candidate_id -> allow | downgrade | veto
    answer: dict | None = None  # the reviewer's validated answer (for the record and the evals)
    error: str | None = None
    latency_ms: int = 0
    cost_usd: float = 0.0

    def multiplier(self, cid: str) -> float:
        """Share of the rules' increase that goes through: 1.0 when the review is off or skipped; per verdict
        when done; 0.0 for every failure (fail closed) and at the monthly hard cap."""
        if self.status in ("off", "skipped"):
            return 1.0
        if self.status != "done":
            return 0.0
        return MULTIPLIER.get(self.verdicts.get(cid, "veto"), 0.0)

    def row(self, rules_plan: Mapping[Key, int], reviewed: Mapping[Key, int], current: Mapping[Key, float]) -> dict:
        """One record per session: both arms of every candidate, so the counterfactual (the rules' plan through
        SimBroker) can be run beside the reviewed plan. Keys are "<strategy>:<symbol>"."""
        keys = sorted(k for k in rules_plan if rules_plan[k] > float(current.get(k, 0) or 0))
        return {"session": self.session.isoformat(), "status": self.status, "error": self.error,
                "verdicts": dict(self.verdicts), "answer": self.answer, "latency_ms": self.latency_ms,
                "cost_usd": round(self.cost_usd, 5),
                "arms": {candidate_id(k): {"current": float(current.get(k, 0) or 0), "rules": int(rules_plan[k]),
                                           "reviewed": int(reviewed[k])} for k in keys}}


def check_answer(answer, session: date, cands: list[ReviewCandidate]) -> tuple[PreopenVerdicts | None, str | None]:
    """Validate the reviewer's answer against the contract and the candidate list. Any doubt -> (None, reason)."""
    if answer is None:
        return None, "no answer (budget, timeout or API error)"
    try:  # a model instance is dumped and validated again: model_construct() would skip every field check
        raw = answer.model_dump(mode="json") if isinstance(answer, PreopenVerdicts) else answer
        v = PreopenVerdicts.model_validate(raw)
    except (ValidationError, TypeError, ValueError) as e:
        return None, f"schema: {str(e)[:200]}"
    if v.session != session.isoformat():
        return None, f"answer is for session {v.session!r}, not {session.isoformat()}"
    want = {c.candidate_id for c in cands}
    got = [x.candidate_id for x in v.verdicts]
    if len(got) != len(set(got)):
        return None, "duplicate candidate ids"
    unknown = sorted(set(got) - want)
    if unknown:
        return None, f"unknown candidates {unknown[:3]}"
    missing = sorted(want - set(got))
    if missing:
        return None, f"no verdict for {missing[:3]}"
    return v, None


def apply(plan: Mapping[Key, int], current: Mapping[Key, float], outcome: ReviewOutcome) -> dict[Key, int]:
    """The reviewed plan. Each growing key becomes current + floor((rules - current) x multiplier) whole shares,
    so it lies between the current position and the rules' target; every other key is the rules' target.
    (A fractional current position counts as its whole shares: a veto never rounds it up. The reviewed target can
    then sit below the fractional holding, e.g. 3 for 3.5 held; whether that trims the fraction is the order
    layer's whole-share rule, not the review's: open question for Backend-quant before wiring.)"""
    out = {}
    for key, tgt in plan.items():
        cur = float(current.get(key, 0) or 0)
        tgt = int(tgt)
        if tgt > cur:
            m = outcome.multiplier(candidate_id(key))
            base = max(0, math.floor(cur))  # a fractional position is never rounded up into an unreviewed buy
            out[key] = min(tgt, base + math.floor((tgt - base) * m))
        else:
            out[key] = tgt
    return out


def review(session: date, targets: Iterable[TargetList], plan: Mapping[Key, int], current: Mapping[Key, float],
           reviewer: Reviewer | None, *, env: Mapping[str, str] | None = None, budget_state: str | None = None,
           total_s: float = TOTAL_S,
           clock=time.monotonic) -> tuple[dict[Key, int], ReviewOutcome, list[ReviewCandidate]]:
    """Run the pre-open review on the builder's plan. Returns (reviewed plan, outcome, candidates)."""
    targets = list(targets)
    if not enabled(env):
        out = ReviewOutcome("off", session)
        return dict(plan), out, []
    cands = candidates(session, targets, plan, current)
    out = ReviewOutcome("done", session)
    if not cands:
        return dict(plan), out, cands
    if reviewer is None:
        out.status, out.error = "error", "pre-open review is on but no reviewer is wired"
    elif budget_state == "no_entries":
        out.status, out.error = "no_entries", "monthly LLM budget reached: no new entries"
    elif budget_state != "ok":
        out.status, out.error = "skipped", f"LLM budget state {budget_state!r}: the rules decide alone"
    elif len(cands) > MAX_CANDIDATES:
        out.status, out.error = "invalid", f"{len(cands)} candidates, more than {MAX_CANDIDATES}"
    else:
        t0 = clock()
        try:
            answer = reviewer(session, cands)
        except Exception as ex:  # a crash in the review is a no-increase, never a crash of the engine
            answer, out.status, out.error = None, "error", str(ex)[:200]
        out.latency_ms = int((clock() - t0) * 1000)
        if out.status == "done" and out.latency_ms > total_s * 1000:
            out.status, out.error = "timeout", f"review took {out.latency_ms} ms (limit {int(total_s * 1000)} ms)"
        if out.status == "done":
            v, err = check_answer(answer, session, cands)
            if v is None:
                out.status, out.error = "invalid", err
            else:
                out.answer = v.model_dump(mode="json")
                out.verdicts = {x.candidate_id: x.verdict for x in v.verdicts}
    return apply(plan, current, out), out, cands


def violations(plan: Mapping[Key, int], current: Mapping[Key, float], reviewed: Mapping[Key, int]) -> list[str]:
    """Independent check of the only-tighten rule; must be empty for every review."""
    bad = []
    for key in sorted(set(plan) | set(reviewed)):
        if key not in plan:
            bad.append(f"{candidate_id(key)}: not in the rules' plan")
            continue
        if key not in reviewed:
            bad.append(f"{candidate_id(key)}: dropped from the plan")
            continue
        cur, tgt, got = float(current.get(key, 0) or 0), int(plan[key]), reviewed[key]
        if not isinstance(got, int):
            bad.append(f"{candidate_id(key)}: not whole shares")
        elif tgt > cur and not (max(0, math.floor(cur)) <= got <= tgt):
            bad.append(f"{candidate_id(key)}: {got} outside [{cur}, {tgt}]")
        elif tgt <= cur and got != tgt:
            bad.append(f"{candidate_id(key)}: a sale or hold was changed ({tgt} -> {got})")
    return bad
