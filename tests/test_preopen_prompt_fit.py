"""#306: the pre-open review prompt was cut at 60 000 characters from about 24 buys with 8 stories each, below
MAX_CANDIDATES = 50, and a cut list made the review fail closed. Now the news shown per candidate is capped (the
same cap for every candidate, the highest that fits) and the prompt is never cut. Offline: FakeStore, FakeLLM and
synthetic lists only. The review stays off; nothing live changes."""

import json
import socket
from datetime import date

import pytest
from fakes import FakeLLM, FakeStore

from kaibot.agents import preopen
from kaibot.agents.evalset import preopen_cost as pc
from kaibot.agents.evalset.preopen import ON_ENV
from kaibot.agents.gateway import Gateway, LLMConfig
from kaibot.agents.preopen import (
    MAX_CANDIDATES,
    MAX_EVIDENCE,
    PREOPEN_TOOL,
    PROMPT_MAX_CHARS,
    GatewayReviewer,
    PromptTooLong,
    candidates,
    fit,
    render,
    review,
    violations,
)
from kaibot.agents.room import _clean_deep, _escape
from kaibot.strategies.base import TargetList

CFG = LLMConfig.load()
D = date(2026, 11, 18)
S = "trend_momentum"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the prompt-fit tests must not touch the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def biggest(n=MAX_CANDIDATES, news=MAX_EVIDENCE, reasons=3):
    """A synthetic list at its largest: n new buys, each with `news` ticker stories whose headline is at the 300
    characters the pack keeps, the rule's reasons repeated for every candidate."""
    syms = [f"SYN{i:02d}" for i in range(n)]
    feats = {s: {"score": 2.0 - i * 0.01, "rank": i + 1, "ret_12_1": 0.5, "earnings_in_days": 30,
                 "sector_etf": "XLK"} for i, s in enumerate(syms)}
    t = TargetList(S, D, {s: 1.0 - i * 0.001 for i, s in enumerate(syms)},
                   reasons=tuple(f"sell OLD{i:02d}: rank fell below the buffer" for i in range(reasons)),
                   features=feats)
    ev = {s: [{"id": f"news:{i * 10 + k}", "scope": "ticker", "source": "synthetic-wire-service-long-name",
               "published_at": f"2026-11-17T{10 + k:02d}:05:00+00:00", "n_outlets": 3,
               "headline": (f"{s} story {k}: " + "a long synthetic wire headline " * 12)[:300]}
              for k in range(news)] for i, s in enumerate(syms)}
    plan = {(S, s): 10 for s in syms}
    return [t], plan, ev, candidates(D, [t], plan, {}, ev)


def old_render(session, cands):
    """The DATA block before #306, verbatim: indented JSON cut at 60 000 characters."""
    data = {"session": session.isoformat(), "candidates": [_clean_deep(c.shown(), 300) for c in cands]}
    return "<data>\n" + _escape(json.dumps(data, default=str, indent=1))[:60_000] + "\n</data>"


def parse(block: str) -> dict:
    return json.loads(block.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])


def model_answer(cite=None):
    def ans(kw):
        data = parse(kw["messages"][0]["content"])
        return {"session": data["session"],
                "verdicts": [{"candidate_id": c["id"], "verdict": "allow", "flag": "none", "confidence": "medium",
                              "reason": "r" * 200, "evidence_ids": [cite] if cite and i == 0 else []}
                             for i, c in enumerate(data["candidates"])]}
    return ans


def test_the_cut_is_reproduced_at_max_candidates_with_the_largest_pack():
    _, _, _, cands = biggest()
    old = old_render(D, cands)
    with pytest.raises(json.JSONDecodeError):  # the old prompt was cut mid-list: no valid list at all
        parse(old)
    assert old.count('"id": "trend_momentum:') < MAX_CANDIDATES  # the model never saw every candidate
    for n, news in ((24, 8), (41, 4)):  # the sizes #304 found
        assert len(old_render(D, biggest(n, news)[3])) - len("<data>\n\n</data>") == 60_000


def test_a_full_list_fits_without_a_cut():
    _, _, _, cands = biggest()
    shown, cap = fit(D, cands)
    block = render(D, shown)
    data = parse(block)  # complete JSON: nothing was cut
    assert len(block) <= PROMPT_MAX_CHARS + len("<data>\n\n</data>")
    assert [c["id"] for c in data["candidates"]] == [c.candidate_id for c in cands]  # every candidate, in order
    assert 0 < cap < MAX_EVIDENCE
    for c, orig in zip(data["candidates"], cands, strict=True):
        assert len(c["evidence"]) == cap and c["evidence_not_shown"] == MAX_EVIDENCE - cap
        assert c["reasons"] == list(orig.reasons) and c["features"] == orig.features  # nothing else dropped
        assert c["features"]["news_7d"] == MAX_EVIDENCE  # the count still says how much news there was
        assert [e["id"] for e in c["evidence"]] == [e["id"] for e in orig.evidence[:cap]]  # the first ones
    assert not fits_with(cands, cap + 1)  # the highest cap that fits


def fits_with(cands, cap):
    return preopen.fits(D, preopen.with_evidence_cap(cands, cap))


def test_a_list_that_fitted_before_is_sent_byte_for_byte_as_before():
    for n, news in ((1, 0), (3, 2), (10, 8), (20, 4)):
        _, _, _, cands = biggest(n, news)
        shown, cap = fit(D, cands)
        assert shown == cands and cap == min(news, MAX_EVIDENCE)
        assert render(D, shown) == old_render(D, cands)


def test_the_full_review_runs_in_one_call_inside_the_budget():
    targets, plan, ev, cands = biggest()
    store = FakeStore()
    llm = FakeLLM({PREOPEN_TOOL["name"]: model_answer()})
    gw = Gateway(store, llm, CFG)
    reviewed, out, shown = review(D, targets, plan, {}, GatewayReviewer(gw), env=ON_ENV, gateway=gw, evidence=ev)
    assert out.status == "done" and len(out.verdicts) == MAX_CANDIDATES and reviewed == plan
    assert len(llm.calls) == 1 and violations(plan, {}, reviewed) == []
    assert all(len(c.evidence) < MAX_EVIDENCE for c in shown)  # the record holds what the reviewer saw
    reserved = store.tables["llm_call"][0]["reserved_usd"]
    assert reserved < CFG.limits["preopen"]["daily_usd"] <= CFG.daily_soft_usd  # inside the $1 day (#309)
    sent = llm.calls[0]["messages"][0]["content"]
    assert len(parse(sent)["candidates"]) == MAX_CANDIDATES


def test_the_answer_for_a_full_list_fits_max_tokens():
    _, _, _, cands = biggest()
    shown, _ = fit(D, cands)
    _, long = pc._answer_tokens(D, shown)  # every reason at 200 characters, the gateway's over-estimate
    assert long <= CFG.limits["preopen"]["max_tokens"]


def test_citing_a_story_that_was_not_shown_is_refused():
    targets, plan, ev, cands = biggest()
    shown, cap = fit(D, cands)
    hidden = cands[0].evidence[cap]["id"]
    store = FakeStore()
    gw = Gateway(store, FakeLLM({PREOPEN_TOOL["name"]: model_answer(cite=hidden)}), CFG)
    reviewed, out, _ = review(D, targets, plan, {}, GatewayReviewer(gw), env=ON_ENV, gateway=gw, evidence=ev)
    assert out.status == "invalid" and "not shown" in out.error
    assert all(reviewed[k] == 0 for k in plan)  # fail closed while ON: no buys


def test_a_list_too_long_even_without_news_fails_closed_and_is_never_cut():
    targets, plan, ev, cands = biggest(reasons=120)  # 120 reasons repeated for 50 candidates
    with pytest.raises(PromptTooLong):
        fit(D, cands)
    with pytest.raises(PromptTooLong):
        render(D, cands)
    store = FakeStore()
    llm = FakeLLM({PREOPEN_TOOL["name"]: model_answer()})
    gw = Gateway(store, llm, CFG)
    reviewed, out, _ = review(D, targets, plan, {}, GatewayReviewer(gw), env=ON_ENV, gateway=gw, evidence=ev)
    assert out.status == "invalid" and "even with no news" in out.error
    assert llm.calls == [] and "llm_call" not in store.tables and all(reviewed[k] == 0 for k in plan)


def test_the_cost_tool_measures_the_full_list():
    session, cands = pc.scenario(MAX_CANDIDATES, MAX_EVIDENCE)
    call = pc.calls_for(pc.variant("today"), session, cands, CFG)[0]
    assert not call.cut_off and call.candidates == MAX_CANDIDATES
    price = CFG.prices[CFG.models["room"]]
    assert call.cost(price, "cache_write", long=True) < 0.15 and call.reserved(price) < CFG.daily_soft_usd
    assert pc.largest_list_that_fits("today", 8) == MAX_CANDIDATES
