"""The pre-open review in SHADOW mode (issue #310; Stage 1, approved by the stakeholder: the AI observes and explains,
it never acts on a trade). Off by default; on only with `KAIBOT_PREOPEN=shadow`. It does not need, read or change
`KAIBOT_ROOM` or `KAIBOT_PREOPEN_REVIEW`.

What it does: once per (rule, session) it is handed a COPY of the plan the rules alone made for the session (the
`md_send` targets, journalled before any order) and the holdings at that moment, runs the pre-open review on the
buys in it (`preopen.review`, one model call through the budget gateway) in a worker thread, and records the
verdicts (allow / downgrade / veto, with the reviewer's reasons). It returns nothing to the daily path: the reviewed
plan `preopen.review` computes is thrown away here, and nothing on the daily path reads a shadow record back.

    send step (15:50 ET)   `engine/live_daily.LiveDaily.send` journals `md_send`, then hands a deep copy of it to
                           `observe` (a worker thread; the send goes on at once, its orders are not waited for)
    every later tick       `collect`: a finished review becomes the journal's `md_review_shadow` entry and the
                           `multi_day_review_shadow` event (the dashboard's feed); `status_block` is the heartbeat's
                           `multi_day.rules[<rule>].review_shadow`

When: on the plan of the session's send step (15:50-15:57 ET, #337), before its near-close orders fill. Not at
08:30: the plan would have to be built a second time before the open, and that read of the announced corporate
actions could leave a cached answer the send step then reuses; an observer must not be able to touch the plan's
inputs.

Failures only log. A missing key, a refused or exhausted budget (the daily cap: the gateway refuses, and the review
is skipped), a timeout or a broken answer is recorded with its status ("skipped" is not a decision of anyone).
Nothing fails closed because nothing acts on the verdicts. trend_momentum's pre-registration has no AI in it
(docs/prereg/trend_momentum.md, Amendment 9).

M12 3.9 (#471), logging mode for the pre-registered negative-news veto (docs/prereg/preopen_negative_news_veto.md):
- every record carries `call`: the pinned model (`models.preopen`), the model that answered, the response id, the
  prompt's sha256, the review's fingerprint and the input sent (`preopen.GatewayReviewer`);
- the same buys are sent a second time with tickers and company names masked (`agents/preopen_mask.py`), after
  the raw arm and only when it answered; that arm is `masked` in the record (its verdicts mapped back to the real
  symbols, its own `call`). `KAIBOT_PREOPEN_MASKED=off` drops it. Both arms go through the same budget gateway.
The raw arm is unchanged: its fields, the event and the heartbeat block are what they were.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from kaibot.agents import preopen
from kaibot.agents.preopen import ReviewOutcome
from kaibot.agents.preopen_daily import EvidenceFn, keys_of, summary, target_view
from kaibot.agents.preopen_mask import MASK_VERSION, Masker
from kaibot.strategies.base import TargetList

log = logging.getLogger("kaibot.preopen.shadow")

FLAG_ENV = "KAIBOT_PREOPEN"
SHADOW = "shadow"
KIND = "md_review_shadow"
EVENT = "multi_day_review_shadow"
MASKED_ENV = "KAIBOT_PREOPEN_MASKED"
STALE_S = 2 * preopen.TOTAL_S + 60.0  # two arms, one after the other, each cut off at TOTAL_S
# preopen.review is a pure function gated by an env mapping; this mapping is handed to it alone (never os.environ)
_REVIEW_ON = {"KAIBOT_ROOM": "on", preopen.FLAG_ENV: "on"}


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """On only with KAIBOT_PREOPEN=shadow. Default: off."""
    from kaibot.common import switches  # M10 1.23 (#448): one parser per switch

    return switches.get(FLAG_ENV, env) == SHADOW


def masked_enabled(env: Mapping[str, str] | None = None) -> bool:
    """The masked arm runs with the shadow unless KAIBOT_PREOPEN_MASKED is off (common/switches.py)."""
    from kaibot.common import switches

    return bool(switches.get(MASKED_ENV, env))


def _frozen(obj):
    """A deep, JSON-safe copy: the observer never holds a reference into the daily path's objects."""
    return json.loads(json.dumps(obj, default=str))


@dataclass
class _Job:
    started: float
    thread: threading.Thread | None = None
    result: dict | None = None
    meta: dict = field(default_factory=dict)


class ShadowDesk:
    """`reviewer`: `preopen.GatewayReviewer(gateway)` live, `preopen_daily.DeterministicReviewer` in tests.
    `gateway`: the LLM budget gateway (budget state and caps are read from it; it refuses over the daily cap).
    `inline=True` runs the review in the caller's thread (tests). `needs_key`: a gateway with no client records
    "no_key" without any budget read."""

    def __init__(self, reviewer, gateway, *, evidence_fn: EvidenceFn | None = None, inline: bool = False,
                 total_s: float = preopen.TOTAL_S, clock: Callable[[], float] = time.monotonic,
                 needs_key: bool = True, masked: bool | None = None) -> None:
        self.reviewer, self.gateway, self.evidence_fn = reviewer, gateway, evidence_fn
        self.masked = masked_enabled() if masked is None else masked
        self.inline, self.total_s, self.clock, self.needs_key = inline, total_s, clock, needs_key
        self._jobs: dict[tuple[str, str], _Job] = {}
        self._lock = threading.Lock()

    def pending(self, rule: str, session: date) -> bool:
        return (rule, str(session)) in self._jobs

    def observe(self, rule: str, session: date, planned: Mapping, tl: TargetList | None,
                held: Mapping[str, tuple[float, float | None]], as_of: datetime, run_id) -> None:
        """Start the shadow review of one rule's plan (once per rule and session). Never raises."""
        try:
            k = (rule, str(session))
            if k in self._jobs:
                return
            planned, held = _frozen(dict(planned)), _frozen(dict(held))
            tl = copy.deepcopy(tl)
            plan, current = keys_of(rule, planned, {s: tuple(v) for s, v in held.items()})
            job = _Job(self.clock(), meta={"run_id": run_id, "strategy": rule, "session": str(session),
                                           "decided": planned.get("decided"), "as_of": as_of.isoformat(),
                                           "mode": SHADOW})
            self._jobs[k] = job
            args = (job, session, tl, plan, current, as_of)
            if self.inline:
                self._run(*args)
                return
            job.thread = threading.Thread(target=self._run, args=args, name=f"preopen-shadow-{rule}", daemon=True)
            job.thread.start()
        except Exception:
            log.exception("shadow pre-open review could not start for %s %s (nothing else is affected)", rule,
                          session)

    def _run(self, job: _Job, session: date, tl, plan, current, as_of) -> None:
        try:
            body = self._review(session, tl, plan, current, as_of)
        except Exception as e:
            log.exception("shadow pre-open review crashed (nothing else is affected)")
            body = record(ReviewOutcome("error", session, error=f"{type(e).__name__}: {e}"[:200]), plan, current,
                          [], tl)
        with self._lock:
            job.result = {**job.meta, **body}

    def _review(self, session, tl, plan, current, as_of) -> dict:
        growing = sorted(k[1] for k, q in plan.items() if q > current.get(k, 0.0))
        if not growing:
            return record(ReviewOutcome("done", session), plan, current, [], tl)
        if self.needs_key and not getattr(self.gateway, "enabled", False):
            return record(ReviewOutcome("no_key", session, error="no ANTHROPIC_API_KEY: shadow review skipped"),
                          plan, current, [], tl)
        evidence = None
        if self.evidence_fn is not None:
            try:
                evidence = {s: list(v or []) for s, v in self.evidence_fn(growing, as_of).items()}
            except Exception:
                log.exception("shadow pre-open evidence unavailable: reviewing without news")
        _reviewed, out, cands = preopen.review(session, [tl] if tl is not None else [], plan, current,
                                               self.reviewer, env=_REVIEW_ON, gateway=self.gateway,
                                               total_s=self.total_s, evidence=evidence)
        # `_reviewed` is deliberately dropped: in shadow mode no reviewed plan exists anywhere
        if out.status != "done":
            log.info("shadow pre-open review %s for %s: %s", out.status, session, out.error)
        rec = record(out, plan, current, cands, tl)
        if self.masked and out.status == "done" and cands:
            rec["masked"] = self._masked(session, tl, plan, current, evidence)
        return rec

    def _masked(self, session, tl, plan, current, evidence) -> dict:
        """The masked arm: the same buys with tickers and names replaced by aliases. Never raises."""
        m = Masker()
        try:
            _r, out, _c = preopen.review(session, [tl] if tl is not None else [], plan, current, self.reviewer,
                                            env=_REVIEW_ON, gateway=self.gateway, total_s=self.total_s,
                                            evidence=evidence, transform=m)
        except Exception as e:
            log.exception("masked shadow arm crashed (nothing else is affected)")
            out = ReviewOutcome("error", session, error=f"{type(e).__name__}: {e}"[:200])
        out.verdicts = m.unmask_verdicts(out.verdicts)
        out.answer = m.unmask_answer(out.answer)
        would = preopen.apply(plan, current, out) if out.status == "done" else dict(plan)
        sm = summary(out, plan, current, would)
        return {"mask_version": MASK_VERSION, "status": skipped(out.status), "review_status": out.status,
                "error": out.error, "verdicts": dict(out.verdicts), "answer": out.answer,
                "latency_ms": out.latency_ms, "cost_usd": round(out.cost_usd, 5), "call": out.call,
                "aliases": dict(m.aliases), "name_masked": dict(m.by_name),
                "would_have": {"vetoed": sm["vetoed"], "downgraded": sm["downgraded"], "allowed": sm["allowed"]}}

    def collect(self, rule: str, session: date) -> dict | None:
        """The finished shadow record of (rule, session), once; None while it runs or when none was started."""
        k = (rule, str(session))
        job = self._jobs.get(k)
        if job is None:
            return None
        with self._lock:
            res = job.result
        if res is None:
            if self.clock() - job.started < STALE_S:
                return None
            res = {**job.meta, **record(ReviewOutcome("timeout", session, error=f"no outcome after {STALE_S:.0f} s"),
                                        {}, {}, [], None)}
        del self._jobs[k]
        return res

    def finished(self) -> list[tuple[str, str]]:
        """(rule, session) of every job that has a result or has gone stale."""
        with self._lock:
            return [k for k, j in self._jobs.items()
                    if j.result is not None or self.clock() - j.started >= STALE_S]


def record(out: ReviewOutcome, plan, current, cands, tl) -> dict:
    """The `md_review_shadow` body: what the reviewer saw and said. `would_have` says what the verdicts would have
    done to the rules' plan had they acted (they did not): vetoed, downgraded, allowed symbols."""
    would = preopen.apply(plan, current, out) if out.status == "done" else dict(plan)
    s = summary(out, plan, current, would)
    return {"status": skipped(out.status), "review_status": out.status, "error": out.error,
            "verdicts": dict(out.verdicts), "answer": out.answer, "latency_ms": out.latency_ms,
            "cost_usd": round(out.cost_usd, 5),
            "buys": {preopen.candidate_id(k): {"current": float(current.get(k, 0) or 0), "rules": int(plan[k])}
                     for k in sorted(plan) if plan[k] > float(current.get(k, 0) or 0)},
            "candidates": [c.shown() for c in cands],
            "targets": target_view(tl, [c.symbol for c in cands]),
            "would_have": {"vetoed": s["vetoed"], "downgraded": s["downgraded"], "allowed": s["allowed"]},
            "call": out.call, "acted": False}


def skipped(status: str) -> str:
    """done | skipped_budget | skipped_no_key | failed (timeout, invalid, error)."""
    if status == "done":
        return "done"
    if status in ("budget", "no_entries"):
        return "skipped_budget"
    if status == "no_key":
        return "skipped_no_key"
    return "failed"


def reasons(rec: Mapping) -> dict[str, str]:
    """symbol -> "<verdict>: <flag>: <reason>" for every candidate the reviewer did not allow."""
    out = {}
    for v in (rec.get("answer") or {}).get("verdicts") or []:
        if v.get("verdict") in ("veto", "downgrade"):
            sym = str(v.get("candidate_id", "")).split(":", 1)[-1]
            out[sym] = f"{v.get('verdict')}: {v.get('flag')}: {v.get('reason')}"[:200]
    return out


def event_body(rec: Mapping) -> dict:
    """The `multi_day_review_shadow` event (replicated to Supabase `event`): small, for the dashboard."""
    w = rec.get("would_have") or {}
    return {**{k: rec.get(k) for k in ("strategy", "session", "status", "review_status", "error", "cost_usd",
                                       "latency_ms")},
            "mode": SHADOW, "acted": False, "would_veto": list(w.get("vetoed") or []),
            "would_downgrade": list(w.get("downgraded") or []), "allowed": list(w.get("allowed") or []),
            "reasons": reasons(rec)}


def status_block(rec: Mapping | None, today: date | None) -> dict:
    """The heartbeat's `multi_day.rules[<rule>].review_shadow`."""
    if rec is None or (today is not None and rec.get("session") != str(today)):
        return {"mode": SHADOW, "status": "not_yet", "note": "shadow review: not run yet today"}
    w = rec.get("would_have") or {}
    st = rec.get("status")
    if st == "done":
        v, d = w.get("vetoed") or [], w.get("downgraded") or []
        note = ("shadow review (observer, no effect): would allow every buy" if not v and not d else
                "shadow review (observer, no effect): would " + ", ".join(
                    ([f"veto {', '.join(v)}"] if v else []) + ([f"halve {', '.join(d)}"] if d else [])))
    else:
        note = f"shadow review {st}: {rec.get('error') or ''}".strip()
    return {"mode": SHADOW, "session": rec.get("session"), "status": st, "would_veto": list(w.get("vetoed") or []),
            "would_downgrade": list(w.get("downgraded") or []), "reasons": reasons(rec),
            "cost_usd": float(rec.get("cost_usd") or 0.0), "note": note[:240]}


__all__ = ["ShadowDesk", "enabled", "event_body", "status_block", "record", "reasons", "KIND", "EVENT",
           "FLAG_ENV", "SHADOW"]
