"""The pre-registered negative-news veto, judged forward only (M12 3.9, #471). Read-only; no model call.

The rules are frozen in docs/prereg/preopen_negative_news_veto.md; the constants below repeat them and a test
checks that they agree. This module only reads the engine's journal (opened read-only) and the committed golden
runs, and says where the test stands. Nothing reads its answer back: the review stays in logging mode, and
turning a veto on is a separate decision of the stakeholder.

Which shadow records count (`eligible`): status done; the call was made with the pinned model and the API says that
model answered; the record's fingerprint is the frozen one; that fingerprint has a passing live run of the golden
set dated on or before the session; and the session is after the freeze date and after the model's knowledge
cutoff. Anything else is left out with its reason (`excluded`).

What is compared: per buy, the return over 5 sessions after the buy's fill (or to the rule's own sale, whichever
first: `shadow_compare.outcomes`). Vetoed = verdict veto with flag company_news; accepted = verdict allow. Downgrades
and other flags are reported, not tested. The statistic is mean(vetoed) - mean(accepted); the veto helps when it is
negative.

Stopping rule: looks at 25 and 50 vetoed buys with an outcome stop for futility when the difference is >= 0; the
final look is at 100 vetoed buys with an outcome or 24 months after the start, whichever comes first. At the final
look the veto passes only if the difference is <= -0.5% (MIN_EFFECT), a one-sided permutation p-value (10 000
shuffles, fixed seed) is below 0.05, and the masked arm's difference has the same sign (else "identity_driven").
Fewer than 25 vetoed buys at 24 months: "inconclusive". Every outcome but "passed" means no veto is proposed.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path

import yaml

from kaibot.agents import shadow_compare as sc
from kaibot.agents.preopen import fingerprint

ROOT = Path(__file__).resolve().parents[3]
PINNED_MODEL = "claude-sonnet-5-5"
FROZEN_FINGERPRINT = "1c5c8833b9de955c109f707918f33e698b0a62c2c995987fc62be32c66e5eaa2"
FREEZE_DATE = date(2026, 11, 22)
HORIZON = 5
LOOKS = (25, 50)
FINAL_N = 100
MAX_MONTHS = 24
MIN_EFFECT = -0.005
ALPHA = 0.05
PERMUTATIONS = 10_000
SEED = 471


def knowledge_cutoff(model: str = PINNED_MODEL, path: Path = ROOT / "config" / "llm.yaml") -> date:
    raw = yaml.safe_load(path.read_text())
    v = (raw.get("knowledge_cutoff") or {}).get(model)
    if v is None:
        raise ValueError(f"no knowledge_cutoff for {model} in config/llm.yaml")
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def start_date(live: Mapping[str, str], cutoff: date | None = None) -> date | None:
    """The first session that can count: after the freeze, after the cutoff, on or after the first live golden run
    of the frozen fingerprint. None while no such run exists."""
    run = live.get(FROZEN_FINGERPRINT)
    if run is None:
        return None
    cutoff = knowledge_cutoff() if cutoff is None else cutoff
    return max(FREEZE_DATE + timedelta(days=1), cutoff + timedelta(days=1), date.fromisoformat(run))


def eligible(rec: Mapping, start: date | None) -> str | None:
    """None when the record counts, else why not."""
    call = rec.get("call") or {}
    if rec.get("status") != "done":
        return f"status {rec.get('status')}"
    if start is None:
        return "no passing live golden run of the frozen fingerprint"
    if call.get("model") != PINNED_MODEL or call.get("served_model") != PINNED_MODEL:
        return f"model {call.get('model')!r} answered by {call.get('served_model')!r}, not {PINNED_MODEL}"
    if call.get("fingerprint") != FROZEN_FINGERPRINT:
        return "fingerprint is not the frozen one"
    if str(rec.get("session")) < start.isoformat():
        return f"session before the start {start}"
    return None


def _flags(rec: Mapping) -> dict[str, str]:
    return {str(v.get("candidate_id", "")).split(":", 1)[-1]: str(v.get("flag"))
            for v in (rec.get("answer") or {}).get("verdicts") or []}


def _arm(rec: Mapping, arm: str) -> dict | None:
    if arm == "raw":
        return dict(rec)
    m = rec.get("masked")
    if not isinstance(m, Mapping) or m.get("status") != "done":
        return None
    return {**rec, "verdicts": m.get("verdicts") or {}, "answer": m.get("answer")}


def groups(records: Iterable[Mapping], fills, marks, arm: str = "raw") -> tuple[list[float], list[float]]:
    """(vetoed returns, accepted returns) of the eligible records, for one arm."""
    recs = [r for r in (_arm(x, arm) for x in records) if r is not None]
    flags = {(str(r.get("strategy")), str(r.get("session"))): _flags(r) for r in recs}
    veto, acc = [], []
    for row in sc.outcomes(recs, fills, marks, HORIZON):
        if row["ret"] is None:
            continue
        f = flags.get((row["strategy"], row["session"]), {}).get(row["symbol"])
        if row["verdict"] == "veto" and f == "company_news":
            veto.append(row["ret"])
        elif row["verdict"] == "allow":
            acc.append(row["ret"])
    return veto, acc


def diff(veto: list[float], acc: list[float]) -> float | None:
    return statistics.fmean(veto) - statistics.fmean(acc) if veto and acc else None


def p_value(veto: list[float], acc: list[float], n: int = PERMUTATIONS, seed: int = SEED) -> float | None:
    """One-sided: the share of label shuffles whose difference is at most the observed one."""
    d = diff(veto, acc)
    if d is None:
        return None
    pool, k, rng, hits = veto + acc, len(veto), random.Random(seed), 0
    for _ in range(n):
        rng.shuffle(pool)
        if statistics.fmean(pool[:k]) - statistics.fmean(pool[k:]) <= d:
            hits += 1
    return (hits + 1) / (n + 1)


def judge(raw: tuple[list[float], list[float]], masked: tuple[list[float], list[float]], start: date | None,
          today: date) -> dict:
    """Where the pre-registered test stands: running | stopped_futility | passed | failed | identity_driven |
    inconclusive | not_started."""
    veto, acc = raw
    n = len(veto)
    d = diff(veto, acc)
    out = {"vetoed": n, "accepted": len(acc), "diff": None if d is None else round(d, 6), "start": None,
           "masked_diff": None, "p_value": None}
    if start is None:
        return {**out, "status": "not_started"}
    out["start"] = start.isoformat()
    end = date(start.year + MAX_MONTHS // 12, start.month, min(start.day, 28))
    for look in LOOKS:
        if n >= look and diff(veto[:look], acc) is not None and diff(veto[:look], acc) >= 0:
            return {**out, "status": "stopped_futility", "look": look}
    final = n >= FINAL_N or today >= end
    if not final:
        return {**out, "status": "running", "final_at": f"{FINAL_N} vetoed buys or {end.isoformat()}"}
    if n < LOOKS[0]:
        return {**out, "status": "inconclusive"}
    v, a = veto[:FINAL_N], acc
    d, p = diff(v, a), p_value(v, a)
    md = diff(*masked)
    out.update({"diff": round(d, 6), "p_value": round(p, 5), "masked_diff": None if md is None else round(md, 6)})
    if not (d <= MIN_EFFECT and p < ALPHA):
        return {**out, "status": "failed"}
    if md is None or md >= 0:
        return {**out, "status": "identity_driven"}
    return {**out, "status": "passed"}


def evaluate(records: list[Mapping], fills, marks, live: Mapping[str, str], today: date,
             cutoff: date | None = None) -> dict:
    start = start_date(live, cutoff)
    ok, excluded = [], {}
    for r in records:
        why = eligible(r, start)
        if why is None:
            ok.append(r)
        else:
            excluded[why] = excluded.get(why, 0) + 1
    res = judge(groups(ok, fills, marks, "raw"), groups(ok, fills, marks, "masked"), start, today)
    return {**res, "records": len(ok), "excluded": excluded}


def main(argv: list[str] | None = None) -> int:
    from kaibot.agents.evalset.golden import live_runs
    from kaibot.agents.preopen_shadow import KIND

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--journal", type=Path, required=True, help="the engine's journal.sqlite (read only)")
    a = ap.parse_args(argv)
    res = evaluate(sc._read(a.journal, KIND), sc._read(a.journal, "fill"), sc._read(a.journal, "mark"),
                   live_runs(), date.today())
    res["current_fingerprint_is_frozen"] = fingerprint(PINNED_MODEL) == FROZEN_FINGERPRINT
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
