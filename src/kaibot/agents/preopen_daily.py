"""The pre-open review on the live daily path (issue #131, M7·S91; decision 15). Off by default.

`PreopenDesk` is what `engine/live_daily.LiveDaily` holds when the review is switched on (`m3glue.make_preopen`
builds it only with KAIBOT_ROOM=on AND KAIBOT_PREOPEN_REVIEW=on; otherwise the daily path gets None and runs line for
line as before). Per (rule, session):

    08:30 ET -> 09:31   `LiveDaily.preopen`: the builder's plan for the session (the same `_plan` the send step
                        uses) goes to `submit`; the review runs in a worker thread (one LLM call through the budget
                        gateway, cut off at `preopen.TOTAL_S`), so the engine's loop never waits on it
    every later tick    `collect`: a finished review becomes the journal's `md_review` entry (both arms of every
                        candidate, the verdicts, the cost) and a `multi_day_review` event (the dashboard's feed)
    09:31 send window   `LiveDaily.send` re-plans, applies the journalled verdicts with `reviewed_plan` and
                        journals `md_send` with a `review` block. A veto drops that buy, a downgrade halves the
                        increase; sales, trims and unchanged positions are never touched. A growing key the review
                        did not see is vetoed (fail closed). With no `md_review` yet the send waits for it (the
                        review has its own hard deadline, so the wait is bounded); after `STALE_S` it is a timeout.

What an agent can do here: veto or halve a buy the rules chose. It never sets a price, a size, a stop or an exit:
the share counts come from the portfolio builder and every reviewed target lies between the holding and the rules'
target (`preopen.apply`, checked again by `preopen.violations` before anything is journalled).

Fail closed while ON (#186, Product for #131): no valid answer for any reason (no ANTHROPIC_API_KEY, daily budget used,
monthly cap, a broken or timed-out answer) skips that session's BUYS; sales always go ahead; the reason is journalled
(`md_review.status`/`error`, the `multi_day_review` event, the heartbeat note). OFF: nothing here exists.

Replay and simulation use `DeterministicReviewer` (no key, no
network, no spend) with `inline=True`, so a SimBroker run is reproducible.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from kaibot.agents import preopen
from kaibot.agents.contracts import CandidateVerdict, Confidence, PreopenVerdicts, ReviewFlag
from kaibot.agents.preopen import Key, ReviewCandidate, ReviewOutcome, candidate_id
from kaibot.strategies.base import TargetList

log = logging.getLogger("kaibot.preopen")

REVIEW_KIND = "md_review"
STALE_S = preopen.TOTAL_S + 60.0  # a job with no outcome after this long is a timeout (the worker died)
EVENT = "multi_day_review"

# (symbols, as_of) -> symbol -> news items (the news desk's evidence pack, ticker scope first)
EvidenceFn = Callable[[list[str], datetime], Mapping[str, Iterable[dict]]]


class DeterministicReviewer:
    """The replay / simulation reviewer: a fixed written policy over what the candidate shows, no model.
    1. earnings in the next 2 sessions    -> veto      (earnings_soon)
    2. a company story in the shown news  -> veto      (company_news; cites the stories)
    3. a pending corporate action         -> downgrade (corporate_action)
    4. otherwise, also with no evidence   -> allow     (none: a replay has no news, the rules decide)
    `veto` / `downgrade`: symbols forced to that verdict (scenario tests of the counterfactual)."""

    def __init__(self, veto: Iterable[str] = (), downgrade: Iterable[str] = ()):
        self.veto, self.downgrade = set(veto), set(downgrade)
        self.calls = 0

    def __call__(self, session: date, cands: list[ReviewCandidate]) -> PreopenVerdicts:
        self.calls += 1
        out = []
        for c in cands:
            f = c.features
            ids = [e["id"] for e in c.evidence if e.get("scope", "ticker") == "ticker"][:8]
            if c.symbol in self.veto:
                v, flag, why, ids = "veto", ReviewFlag.UNKNOWN, "scenario veto", []
            elif c.symbol in self.downgrade:
                v, flag, why, ids = "downgrade", ReviewFlag.UNKNOWN, "scenario downgrade", []
            elif isinstance(f.get("earnings_in_days"), int | float) and 0 <= f["earnings_in_days"] <= 2:
                v, flag, why, ids = "veto", ReviewFlag.EARNINGS_SOON, "earnings inside the first holding days", []
            elif (f.get("news_7d") or 0) > 0:
                v, flag, why = "veto", ReviewFlag.COMPANY_NEWS, "company story in the shown news"
            elif f.get("corporate_action"):
                v, flag, why, ids = "downgrade", ReviewFlag.CORPORATE_ACTION, "pending corporate action", []
            else:
                v, flag, why, ids = "allow", ReviewFlag.NONE, "nothing found", []
            out.append(CandidateVerdict(candidate_id=c.candidate_id, verdict=v, flag=flag,
                                        confidence=Confidence.MEDIUM, reason=why, evidence_ids=ids))
        return PreopenVerdicts(session=session.isoformat(), verdicts=out)


def keys_of(rule: str, planned: Mapping, held: Mapping[str, tuple[float, float | None]]
            ) -> tuple[dict[Key, int], dict[Key, float]]:
    """The rules' plan and the holdings of an `md_send`-shaped body, per (strategy, symbol)."""
    plan = {(rule, s): int(q) for s, q, _, _ in planned.get("targets") or []}
    current = {(rule, s): float(v[0]) for s, v in held.items()}
    return plan, current


def target_view(tl: TargetList | None, symbols: Iterable[str]) -> list[dict]:
    """The target list as the journal keeps it for the offline evals (`evalset/preopen.from_journal`)."""
    if tl is None:
        return []
    syms = set(symbols)
    feats = {s: v for s, v in (tl.features or {}).items() if s in syms and isinstance(v, Mapping)}
    return [{"strategy": tl.strategy, "weights": {s: float(w) for s, w in tl.weights.items()},
             "reasons": list(tl.reasons), "features": feats}]


@dataclass
class _Job:
    started: float
    thread: threading.Thread | None = None
    result: dict | None = None
    meta: dict = field(default_factory=dict)


class PreopenDesk:
    """`reviewer`: the session's reviewer (`preopen.GatewayReviewer` live, `DeterministicReviewer` in replays).
    `gateway`: the LLM budget gateway (`agents/gateway.Gateway`; `evalset.preopen.offline_gateway()` offline).
    `env`: the switches (default os.environ). `evidence_fn`: news per symbol (None: no news shown).
    `inline=True` runs the review in the caller's thread (replays: reproducible, no threads)."""

    def __init__(self, reviewer, gateway, *, env: Mapping[str, str] | None = None,
                 evidence_fn: EvidenceFn | None = None, inline: bool = False, total_s: float = preopen.TOTAL_S,
                 clock: Callable[[], float] = time.monotonic, needs_key: bool = True) -> None:
        self.reviewer, self.gateway, self.env = reviewer, gateway, env
        self.evidence_fn, self.inline, self.total_s, self.clock = evidence_fn, inline, total_s, clock
        self.needs_key = needs_key  # live: a gateway with no client (no ANTHROPIC_API_KEY) skips the review
        self._jobs: dict[tuple[str, str], _Job] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return preopen.enabled(self.env)

    def pending(self, rule: str, session: date) -> bool:
        return (rule, str(session)) in self._jobs

    def submit(self, rule: str, session: date, planned: Mapping, tl: TargetList | None,
               held: Mapping[str, tuple[float, float | None]], as_of: datetime, run_id) -> None:
        """Start the review of one rule's plan for `session` (once: a second submit while it runs is ignored)."""
        k = (rule, str(session))
        if k in self._jobs:
            return
        plan, current = keys_of(rule, planned, held)
        job = _Job(self.clock(), meta={"run_id": run_id, "strategy": rule, "session": str(session),
                                       "decided": planned.get("decided"), "as_of": as_of.isoformat()})
        self._jobs[k] = job
        args = (job, session, tl, plan, current, as_of)
        if self.inline:
            self._run(*args)
            return
        job.thread = threading.Thread(target=self._run, args=args, name=f"preopen-{rule}", daemon=True)
        job.thread.start()

    def _run(self, job: _Job, session: date, tl: TargetList | None, plan: dict[Key, int],
             current: dict[Key, float], as_of: datetime) -> None:
        """The worker: never raises; whatever happens becomes an outcome (failures fail closed in `preopen`)."""
        try:
            body = self._review(session, tl, plan, current, as_of)
        except Exception as e:  # a bug here is a no-increase, never a crash of the engine
            log.exception("pre-open review crashed")
            out = ReviewOutcome("error", session, error=f"{type(e).__name__}: {e}"[:200])
            body = self._body(out, plan, current, preopen.apply(plan, current, out), [], tl)
        with self._lock:
            job.result = {**job.meta, **body}

    def _review(self, session, tl, plan, current, as_of) -> dict:
        growing = sorted(k[1] for k, q in plan.items() if q > current.get(k, 0.0))
        if not growing:  # nothing to review: no call, no budget read
            out = ReviewOutcome("done", session)
            return self._body(out, plan, current, dict(plan), [], tl)
        if self.needs_key and not getattr(self.gateway, "enabled", False):
            out = ReviewOutcome("no_key", session, error="no ANTHROPIC_API_KEY: no buys this session")
            log.warning("pre-open review %s for %s: %s", out.status, session, out.error)
            return self._body(out, plan, current, preopen.apply(plan, current, out), [], tl)
        evidence = None
        if self.evidence_fn is not None:
            try:
                evidence = {s: list(v or []) for s, v in self.evidence_fn(growing, as_of).items()}
            except Exception:  # no news is shown, not invented: the reviewer sees no evidence for any candidate
                log.exception("pre-open evidence unavailable: reviewing without news")
                evidence = None
        reviewed, out, cands = preopen.review(session, [tl] if tl is not None else [], plan, current,
                                              self.reviewer, env=self.env, gateway=self.gateway,
                                              total_s=self.total_s, evidence=evidence)
        return self._body(out, plan, current, reviewed, cands, tl)

    @staticmethod
    def _body(out: ReviewOutcome, plan, current, reviewed, cands: list[ReviewCandidate], tl) -> dict:
        bad = preopen.violations(plan, current, reviewed)
        if bad:  # cannot happen (apply clamps); if it ever does, nothing grows
            out = ReviewOutcome("error", out.session, error=f"only-tighten check failed: {bad[:2]}")
            reviewed = preopen.apply(plan, current, out)
        row = out.row(plan, reviewed, current)
        row.update(candidates=[c.shown() for c in cands], targets=target_view(tl, [c.symbol for c in cands]),
                   **summary(out, plan, current, reviewed))
        return row

    def collect(self, rule: str, session: date) -> dict | None:
        """The finished review of (rule, session) as an `md_review` body (once), or None while it runs. A job that
        has run past STALE_S becomes a timeout (no increase)."""
        k = (rule, str(session))
        job = self._jobs.get(k)
        if job is None:
            return None
        with self._lock:
            res = job.result
        if res is None:
            if self.clock() - job.started < STALE_S:
                return None
            log.error("pre-open review of %s for %s has no outcome after %.0f s: timeout", rule, session, STALE_S)
            res = {**job.meta, "status": "timeout", "error": f"no outcome after {STALE_S:.0f} s",
                   "verdicts": {}, "answer": None, "latency_ms": int(STALE_S * 1000), "cost_usd": 0.0, "arms": {},
                   "candidates": [], "targets": [], "vetoed": [], "downgraded": [], "allowed": [],
                   "pass_through": False}
        del self._jobs[k]
        return res


def summary(out: ReviewOutcome, plan, current, reviewed) -> dict:
    """What the dashboard shows: which buys were dropped, halved or kept, and whether the rules went alone."""
    vetoed, downgraded, allowed = [], [], []
    for key in sorted(plan):
        cur, tgt = float(current.get(key, 0) or 0), int(plan[key])
        if tgt <= cur:
            continue
        got = reviewed[key]
        (allowed if got == tgt else vetoed if got <= cur else downgraded).append(key[1])
    return {"vetoed": vetoed, "downgraded": downgraded, "allowed": allowed,
            "pass_through": out.status in preopen.PASS_THROUGH}


def reviewed_plan(rule: str, planned: dict, held: Mapping[str, tuple[float, float | None]],
                  record: Mapping) -> tuple[dict, dict]:
    """Apply a journalled `md_review` to a fresh plan (the send step's). Returns (the `md_send` body, its `review`
    block). A vetoed buy (or one floored to the holding) is dropped from the targets: the target loop sends nothing
    for a symbol it is not given, and the holding stays as it is. A downgrade sends the reviewed whole shares.
    "off" keeps the plan; every other non-done status skips every buy; a growing key the review did not see is
    vetoed."""
    session = date.fromisoformat(str(record["session"]))
    out = ReviewOutcome(str(record.get("status")), session, verdicts=dict(record.get("verdicts") or {}))
    plan, current = keys_of(rule, planned, held)
    reviewed = preopen.apply(plan, current, out)
    bad = preopen.violations(plan, current, reviewed)
    if bad:  # never expected; fail closed: nothing grows
        out = ReviewOutcome("error", session)
        reviewed = preopen.apply(plan, current, out)
    targets, dropped, cut = [], [], {}
    for s, q, p, w in planned.get("targets") or []:
        key = (rule, s)
        cur, got = current.get(key, 0.0), reviewed.get(key, q)
        if int(q) > cur and got != int(q):
            if got <= cur:
                dropped.append(s)
                continue
            cut[s] = [int(q), int(got)]
            targets.append([s, int(got), p, f"{w};preopen_downgrade" if w else "preopen_downgrade"])
            continue
        targets.append([s, q, p, w])
    block = {"status": out.status, "vetoed": dropped, "downgraded": cut,
             "unseen": sorted(cid.split(":", 1)[1] for cid in (candidate_id(k) for k in plan
                                                                if plan[k] > current.get(k, 0.0))
                              if out.status == "done" and cid not in out.verdicts),
             "cost_usd": float(record.get("cost_usd") or 0.0)}
    return {**planned, "targets": targets, "review": block}, block


def event_body(record: Mapping) -> dict:
    """The `multi_day_review` event (replicated to Supabase `event`): small, for the dashboard."""
    return {k: record.get(k) for k in ("strategy", "session", "status", "error", "vetoed", "downgraded", "allowed",
                                       "pass_through", "cost_usd", "latency_ms")} | {"reasons": _reasons(record)}


def _reasons(record: Mapping) -> dict[str, str]:
    """symbol -> the reviewer's short reason for every vetoed or downgraded candidate (from the validated answer)."""
    ans = record.get("answer") or {}
    out = {}
    for v in ans.get("verdicts") or []:
        if v.get("verdict") in ("veto", "downgrade"):
            sym = str(v.get("candidate_id", "")).split(":", 1)[-1]
            out[sym] = f"{v.get('verdict')}: {v.get('flag')}: {v.get('reason')}"[:200]
    return out


def status_block(record: Mapping | None, today: date | None) -> dict | None:
    """The heartbeat's `multi_day.rules[<rule>].review` (the dashboard's "pre-open review" line)."""
    if record is None or (today is not None and record.get("session") != str(today)):
        return None
    st = record.get("status")
    if st in preopen.PASS_THROUGH:
        note = "pre-open review off"
    elif st == "done":
        v, d = record.get("vetoed") or [], record.get("downgraded") or []
        note = ("pre-open review: no change" if not v and not d else
                "pre-open review: " + ", ".join(([f"vetoed {', '.join(v)}"] if v else [])
                                                + ([f"halved {', '.join(d)}"] if d else [])))
    else:
        note = f"no new buys on {record.get('session')}: pre-open review {st} ({record.get('error') or 'fail closed'})"
    return {"session": record.get("session"), "status": st, "vetoed": list(record.get("vetoed") or []),
            "downgraded": list(record.get("downgraded") or []), "reasons": _reasons(record),
            "cost_usd": float(record.get("cost_usd") or 0.0), "note": note[:240]}


__all__ = ["PreopenDesk", "DeterministicReviewer", "reviewed_plan", "event_body", "status_block", "REVIEW_KIND",
           "EVENT", "STALE_S"]
