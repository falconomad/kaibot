"""Offline evals of the pre-open review (issue #131) on stored candidate lists. No network, no key, no spend.

A stored list, one JSON object per line (format version 1):

    {"format_version": 1, "case_id": "pre-001", "synthetic": true,
     "session": "2026-11-13",
     "targets": [{"strategy": "...", "weights": {"SYM": 0.5}, "reasons": [...], "features": {"SYM": {...}}}],
     "current": {"strategy:SYM": 0},           positions before the session
     "plan": {"strategy:SYM": 40},             the portfolio builder's whole-share targets (the rules' plan)
     "expected": {"strategy:SYM": "veto"}}     optional: what the stub's written rules require (synthetic only)

`StubReviewer` applies a fixed written policy to the shown features, so the plumbing (contract, fail-closed paths,
only-tighten clamp) can be regression-tested; it says nothing about how good a real model is. Counterfactual
P&L is not computed here: `ReviewOutcome.row()` carries both arms, and the engine replays them through SimBroker.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

from kaibot.agents.contracts import CandidateVerdict, Confidence, PreopenVerdicts, ReviewFlag
from kaibot.agents.evalset.runner import OfflineBudget
from kaibot.agents.gateway import Gateway, LLMConfig
from kaibot.agents.preopen import Key, ReviewCandidate, review, violations
from kaibot.strategies.base import TargetList

FORMAT_VERSION = 1
PREOPEN_SET = Path(__file__).with_name("preopen_v1.jsonl")
ON_ENV = {"KAIBOT_ROOM": "on", "KAIBOT_PREOPEN_REVIEW": "on"}  # only ever passed explicitly, never set in os.environ


class StubReviewer:
    """Deterministic policy over the shown features:
    1. earnings in the next 2 sessions            -> veto      (earnings_soon)
    2. a company story in the last 7 days         -> veto      (company_news)
    3. a pending corporate action                 -> downgrade (corporate_action)
    4. no features at all                         -> downgrade (data_issue)
    5. otherwise                                  -> allow     (none)"""

    def __init__(self):
        self.calls = 0

    def __call__(self, session: date, cands: list[ReviewCandidate]) -> PreopenVerdicts:
        self.calls += 1
        out = []
        for c in cands:
            f = c.features
            if isinstance(f.get("earnings_in_days"), int | float) and 0 <= f["earnings_in_days"] <= 2:
                v, flag, why = "veto", ReviewFlag.EARNINGS_SOON, "earnings inside the first holding days"
            elif (f.get("news_7d") or 0) > 0:
                v, flag, why = "veto", ReviewFlag.COMPANY_NEWS, "company story in the last 7 days"
            elif f.get("corporate_action"):
                v, flag, why = "downgrade", ReviewFlag.CORPORATE_ACTION, "pending corporate action"
            elif not f:
                v, flag, why = "downgrade", ReviewFlag.DATA_ISSUE, "no evidence shown"
            else:
                v, flag, why = "allow", ReviewFlag.NONE, "nothing found"
            out.append(CandidateVerdict(candidate_id=c.candidate_id, verdict=v, flag=flag,
                                        confidence=Confidence.MEDIUM, reason=why))
        return PreopenVerdicts(session=session.isoformat(), verdicts=out)


def _key(s: str) -> Key:
    strat, _, sym = s.partition(":")
    if not strat or not sym:
        raise ValueError(f"bad key {s!r}: expected 'strategy:SYMBOL'")
    return strat, sym


def load_lists(path: Path = PREOPEN_SET) -> list[dict]:
    out, seen = [], set()
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw.get("format_version") != FORMAT_VERSION:
            raise ValueError(f"line {n}: format_version {raw.get('format_version')!r}, expected {FORMAT_VERSION}")
        if raw["case_id"] in seen:
            raise ValueError(f"line {n}: duplicate case_id {raw['case_id']}")
        seen.add(raw["case_id"])
        session = date.fromisoformat(raw["session"])
        raw["session"] = session
        raw["targets"] = [TargetList(t["strategy"], session, dict(t["weights"]), reasons=tuple(t.get("reasons", ())),
                                     features=dict(t.get("features") or {})) for t in raw["targets"]]
        raw["current"] = {_key(k): float(v) for k, v in (raw.get("current") or {}).items()}
        raw["plan"] = {_key(k): int(v) for k, v in raw["plan"].items()}
        out.append(raw)
    return out


def offline_gateway() -> Gateway:
    """A budget gateway on the in-memory budget (state ok, never refuses) and with no client: no key, no spend."""
    return Gateway(OfflineBudget(), None, LLMConfig.load())


def evaluate(lists: list[dict], reviewer=None, gateway=None) -> dict:
    """Run the review over stored lists. `contract_violations` must be 0. The budget state comes from `gateway`
    (default: the offline one, always ok)."""
    reviewer = reviewer or StubReviewer()
    gateway = gateway or offline_gateway()
    statuses, verdicts, bad, match, n_expected, n_cands = Counter(), Counter(), [], 0, 0, 0
    rows = []
    for case in lists:
        reviewed, out, cands = review(case["session"], case["targets"], case["plan"], case["current"], reviewer,
                                      env=ON_ENV, gateway=gateway)
        statuses[out.status] += 1
        n_cands += len(cands)
        verdicts.update(out.verdicts.values())
        bad += [f"{case['case_id']}: {v}" for v in violations(case["plan"], case["current"], reviewed)]
        for cid, want in (case.get("expected") or {}).items():
            n_expected += 1
            match += out.verdicts.get(cid) == want
        rows.append({"case_id": case["case_id"], **out.row(case["plan"], reviewed, case["current"])})
    return {"lists": len(lists), "candidates": n_cands, "statuses": dict(statuses), "verdicts": dict(verdicts),
            "contract_violations": bad, "expected_match": (match / n_expected) if n_expected else None,
            "rows": rows}
