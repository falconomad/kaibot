"""Issue #131 (M7·S91), contracts-first slice: the pre-open review of a daily rule's candidate list.
Offline: no network, no key, no LLM call. The review may only veto or halve an increase."""

import socket
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from kaibot.agents import preopen
from kaibot.agents.contracts import CandidateVerdict, Confidence, PreopenVerdicts, ReviewFlag
from kaibot.agents.evalset.preopen import ON_ENV, PREOPEN_SET, StubReviewer, evaluate, load_lists
from kaibot.agents.preopen import MAX_CANDIDATES, apply, candidates, check_answer, enabled, review, violations
from kaibot.agents.room import FORBIDDEN_FIELDS, _field_names, tool_for
from kaibot.strategies.base import TargetList

D = date(2026, 11, 13)
S = "trend_momentum"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the pre-open review tests must not touch the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def tl(weights, features=None, strategy=S):
    return TargetList(strategy, D, weights, reasons=("r",), features=features or {})


def answer(cands, verdict="allow", session=D, **over):
    vs = [{"candidate_id": c.candidate_id, "verdict": over.get(c.symbol, verdict), "flag": "none",
           "confidence": "medium", "reason": "x"} for c in cands]
    return {"session": session.isoformat(), "verdicts": vs}


# ---------- contract ----------

def test_contract_has_no_price_size_stop_or_exit_fields():
    for model in (PreopenVerdicts, CandidateVerdict):
        assert not (_field_names(model.model_json_schema()) & FORBIDDEN_FIELDS)
        tool_for("submit_preopen_review", model, "x")  # raises on a forbidden field


def test_contract_rejects_extra_fields_and_other_verdicts():
    base = {"candidate_id": "a:B", "verdict": "allow", "flag": "none", "confidence": "low", "reason": "x"}
    CandidateVerdict.model_validate(base)
    with pytest.raises(ValidationError):
        CandidateVerdict.model_validate({**base, "size": 10})
    with pytest.raises(ValidationError):
        CandidateVerdict.model_validate({**base, "verdict": "double"})
    with pytest.raises(ValidationError):
        PreopenVerdicts.model_validate({"session": "2026-11-13", "verdicts": [], "stop": 1.0})


# ---------- off by default ----------

def test_off_by_default():
    assert not enabled({})
    assert not enabled({"KAIBOT_ROOM": "on"})
    assert not enabled({"KAIBOT_PREOPEN_REVIEW": "on"})
    assert enabled({"KAIBOT_ROOM": "on", "KAIBOT_PREOPEN_REVIEW": "on"})


def test_off_calls_nothing_and_keeps_the_plan(monkeypatch):
    monkeypatch.delenv("KAIBOT_ROOM", raising=False)
    monkeypatch.delenv("KAIBOT_PREOPEN_REVIEW", raising=False)
    rev = StubReviewer()
    plan = {(S, "SYNA"): 40}
    out_plan, out, cands = review(D, [tl({"SYNA": 1.0})], plan, {}, rev)
    assert (out_plan, out.status, cands, rev.calls) == (plan, "off", [], 0)


# ---------- candidates ----------

def test_only_increases_are_candidates_ranked_by_weight():
    t = tl({"A": 0.2, "B": 0.5, "C": 0.5}, {"B": {"score": 2.0, "earnings_in_days": 9, "atr": 3.1, "px": 10}})
    plan = {(S, "A"): 10, (S, "B"): 20, (S, "C"): 5, (S, "H"): 0, ("other", "Z"): 3}
    cur = {(S, "A"): 4, (S, "C"): 5, (S, "H"): 8, ("other", "Z"): 0}
    cs = candidates(D, [t], plan, cur)
    assert [(c.candidate_id, c.kind, c.rank) for c in cs] == [("other:Z", "new", None), (f"{S}:B", "new", 1),
                                                              (f"{S}:A", "add", 3)]
    b = cs[1]
    assert b.features == {"score": 2.0, "earnings_in_days": 9}  # no ATR or price shown
    assert "shares" not in b.shown() and "px" not in str(b.shown())


# ---------- verdicts and fail-closed paths ----------

def run(rev, plan=None, cur=None, budget="ok", weights=None):
    plan = plan or {(S, "A"): 40, (S, "B"): 21, (S, "C"): 10}
    cur = cur if cur is not None else {(S, "B"): 1}
    return review(D, [tl(weights or {"A": 0.5, "B": 0.3, "C": 0.2})], plan, cur, rev, env=ON_ENV,
                  budget_state=budget)


def test_allow_downgrade_veto():
    reviewed, out, cands = run(lambda d, cs: answer(cs, A="allow", B="downgrade", C="veto"))
    assert out.status == "done"
    assert reviewed == {(S, "A"): 40, (S, "B"): 11, (S, "C"): 0}  # B: 1 + floor(20 / 2)


@pytest.mark.parametrize("bad, why", [
    (lambda d, cs: None, "no answer"),
    (lambda d, cs: answer(cs, session=date(2026, 11, 12)), "session"),
    (lambda d, cs: answer(cs[:-1]), "no verdict"),
    (lambda d, cs: {**answer(cs), "verdicts": answer(cs)["verdicts"] + answer(cs)["verdicts"][:1]}, "duplicate"),
    (lambda d, cs: {**answer(cs), "verdicts": answer(cs)["verdicts"] + [{**answer(cs)["verdicts"][0],
                                                                         "candidate_id": f"{S}:ZZZ"}]}, "unknown"),
    (lambda d, cs: {"session": d.isoformat(), "verdicts": [{"candidate_id": c.candidate_id, "verdict": "allow",
                                                            "flag": "none", "confidence": "low", "reason": "x",
                                                            "qty": 999} for c in cs]}, "schema"),
])
def test_bad_answers_veto_every_increase(bad, why):
    reviewed, out, _ = run(bad)
    assert out.status == "invalid" and why in out.error
    assert reviewed == {(S, "A"): 0, (S, "B"): 1, (S, "C"): 0}


def test_reviewer_crash_is_no_increase_not_a_crash():
    def boom(d, cs):
        raise RuntimeError("api down")

    reviewed, out, _ = run(boom)
    assert out.status == "error" and reviewed == {(S, "A"): 0, (S, "B"): 1, (S, "C"): 0}


def test_budget_soft_cap_or_unknown_skips_and_hard_cap_blocks():
    rev = StubReviewer()
    for state in ("rules_only", None):
        reviewed, out, _ = run(rev, budget=state)
        assert out.status == "skipped" and reviewed == {(S, "A"): 40, (S, "B"): 21, (S, "C"): 10}
    reviewed, out, _ = run(rev, budget="no_entries")
    assert out.status == "no_entries" and reviewed == {(S, "A"): 0, (S, "B"): 1, (S, "C"): 0}
    assert rev.calls == 0


def test_too_many_candidates_fails_closed():
    n = MAX_CANDIDATES + 1
    plan = {(S, f"S{i:03d}"): 5 for i in range(n)}
    rev = StubReviewer()
    reviewed, out, _ = run(rev, plan=plan, cur={}, weights={k[1]: 0.1 for k in plan})
    assert out.status == "invalid" and set(reviewed.values()) == {0} and rev.calls == 0


def test_no_candidates_no_call():
    rev = StubReviewer()
    reviewed, out, cands = run(rev, plan={(S, "A"): 3}, cur={(S, "A"): 5})
    assert reviewed == {(S, "A"): 3} and cands == [] and rev.calls == 0


def test_on_without_a_reviewer_fails_closed():
    reviewed, out, cands = run(None)
    assert out.status == "error" and "no reviewer" in out.error and len(cands) == 3
    assert reviewed == {(S, "A"): 0, (S, "B"): 1, (S, "C"): 0}


def test_slow_review_times_out_and_vetoes_every_increase():
    ticks = iter([0.0, preopen.TOTAL_S + 1.0])
    reviewed, out, _ = review(D, [tl({"A": 0.5, "B": 0.3, "C": 0.2})], {(S, "A"): 40, (S, "B"): 21, (S, "C"): 10},
                              {(S, "B"): 1}, lambda d, cs: answer(cs), env=ON_ENV, budget_state="ok",
                              clock=lambda: next(ticks))
    assert out.status == "timeout" and out.verdicts == {}
    assert reviewed == {(S, "A"): 0, (S, "B"): 1, (S, "C"): 0}


def test_unvalidated_model_instance_is_rejected():
    cs = candidates(D, [tl({"A": 1.0})], {(S, "A"): 5}, {})
    bad = PreopenVerdicts.model_construct(session=D.isoformat(), verdicts=[
        CandidateVerdict.model_construct(candidate_id=f"{S}:A", verdict="double", flag=ReviewFlag.NONE,
                                         confidence=Confidence.LOW, reason="x", evidence_ids=[])])
    v, err = check_answer(bad, D, cs)
    assert v is None and err.startswith("schema")


def test_check_answer_accepts_the_model():
    cs = candidates(D, [tl({"A": 1.0})], {(S, "A"): 5}, {})
    v, err = check_answer(PreopenVerdicts.model_validate(answer(cs)), D, cs)
    assert err is None and v.verdicts[0].candidate_id == f"{S}:A"


def test_row_carries_both_arms():
    reviewed, out, _ = run(lambda d, cs: answer(cs, C="veto"))
    row = out.row({(S, "A"): 40, (S, "B"): 21, (S, "C"): 10}, reviewed, {(S, "B"): 1})
    assert row["arms"][f"{S}:C"] == {"current": 0.0, "rules": 10, "reviewed": 0}
    assert row["verdicts"][f"{S}:C"] == "veto"


# ---------- property: the review only ever tightens ----------

keys = st.tuples(st.sampled_from(["s1", "s2"]), st.sampled_from(list("ABCDEFG")))


@settings(max_examples=300, deadline=None)
@given(plan=st.dictionaries(keys, st.integers(0, 500), max_size=10),
       cur=st.dictionaries(keys, st.one_of(st.integers(0, 500), st.floats(0, 500)), max_size=10),
       picks=st.lists(st.sampled_from(["allow", "downgrade", "veto"]), min_size=10, max_size=10),
       status=st.sampled_from(preopen.STATUSES))
def test_property_only_tightens(plan, cur, picks, status):
    out = preopen.ReviewOutcome(status, D, verdicts={preopen.candidate_id(k): picks[i % 10]
                                                     for i, k in enumerate(sorted(plan))})
    reviewed = apply(plan, cur, out)
    assert violations(plan, cur, reviewed) == []
    for k, tgt in plan.items():
        c = float(cur.get(k, 0))
        if tgt <= c:
            assert reviewed[k] == tgt  # sales and holds untouched
        else:
            assert c <= reviewed[k] <= tgt  # never below the holding: the review never sells
            assert isinstance(reviewed[k], int) or reviewed[k] == c  # whole shares, or the holding unchanged
            if out.multiplier(preopen.candidate_id(k)) == 0.0:
                assert reviewed[k] == c  # a veto (or a failure) sends no order at all


@pytest.mark.parametrize("verdict, want", [("allow", 4), ("downgrade", 3.5), ("veto", 3.5)])
def test_fractional_holding_is_never_sold_by_the_review(verdict, want):
    k = (S, "A")
    reviewed, out, _ = review(D, [tl({"A": 1.0})], {k: 4}, {k: 3.5}, lambda d, cs: answer(cs, verdict),
                              env=ON_ENV, budget_state="ok")
    assert out.status == "done" and reviewed == {k: want}
    assert violations({k: 4}, {k: 3.5}, reviewed) == []
    assert out.row({k: 4}, reviewed, {k: 3.5})["arms"][f"{S}:A"]["reviewed"] == want


def test_fractional_holding_fail_closed_keeps_the_holding():
    k = (S, "A")
    reviewed, out, _ = review(D, [tl({"A": 1.0})], {k: 9}, {k: 3.5}, lambda d, cs: None, env=ON_ENV,
                              budget_state="ok")
    assert out.status == "invalid" and reviewed == {k: 3.5}


def test_violations_catches_a_loosening():
    plan, cur = {(S, "A"): 10, (S, "B"): 0}, {(S, "B"): 5}
    assert violations(plan, cur, {(S, "A"): 11, (S, "B"): 0})
    assert violations(plan, cur, {(S, "A"): 10, (S, "B"): 5})
    assert violations(plan, cur, {(S, "A"): 10})
    assert violations(plan, cur, {(S, "A"): 10, (S, "B"): 0, (S, "C"): 1})
    frac = {(S, "A"): 3.5}
    assert violations({(S, "A"): 4}, frac, {(S, "A"): 3})  # below the holding: a sale decided by the review
    assert violations({(S, "A"): 4}, frac, {(S, "A"): 3.75})  # neither whole shares nor the holding
    assert violations({(S, "A"): 4}, frac, {(S, "A"): 3.5}) == []


# ---------- offline evals on stored candidate lists ----------

def test_stored_lists_offline_eval():
    lists = load_lists(PREOPEN_SET)
    assert len(lists) >= 6 and all(case["synthetic"] for case in lists)
    rep = evaluate(lists)
    assert rep["contract_violations"] == []
    assert rep["expected_match"] == 1.0
    assert rep["statuses"] == {"done": len(lists)}
    sells = next(r for r in rep["rows"] if r["case_id"] == "pre-005")
    assert sells["arms"] == {} and sells["verdicts"] == {}


@pytest.mark.parametrize("state", ["rules_only", "no_entries"])
def test_stored_lists_under_budget_states(state):
    rep = evaluate(load_lists(PREOPEN_SET), budget_state=state)
    assert rep["contract_violations"] == []
    assert rep["verdicts"] == {}


def test_load_lists_rejects_bad_format(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"format_version": 2, "case_id": "a"}\n')
    with pytest.raises(ValueError, match="format_version"):
        load_lists(p)
