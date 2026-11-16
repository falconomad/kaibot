"""Issue #131 (M7·S91): the pre-open review desk of the daily path (agents/preopen_daily.py) with fakes only.
No network, no key, no spend: FakeStore mirrors the budget RPCs, FakeLLM stands in for the API."""

import json
import socket
import threading
from datetime import UTC, date, datetime

import pytest
from fakes import FakeLLM, FakeStore

from kaibot.agents import preopen
from kaibot.agents.evalset.preopen import ON_ENV, offline_gateway
from kaibot.agents.gateway import Gateway, LLMConfig
from kaibot.agents.preopen import PREOPEN_TOOL, GatewayReviewer, check_answer, review
from kaibot.agents.preopen_daily import (
    STALE_S,
    DeterministicReviewer,
    PreopenDesk,
    event_body,
    reviewed_plan,
    status_block,
)
from kaibot.engine.m3glue import make_preopen, make_preopen_evidence
from kaibot.strategies.base import TargetList

D = date(2026, 11, 17)
R = "trend_momentum"
NOW = datetime(2026, 11, 17, 13, 45, tzinfo=UTC)
PLANNED = {"run_id": 1, "strategy": R, "session": str(D), "decided": "2026-11-16",
           "targets": [["A", 40, "target", "buy"], ["B", 21, "target", "resize"], ["H", 2, "target", "trim"],
                       ["Z", 0, "target", "sell"]]}
HELD = {"B": (1.0, 10.0), "H": (8.0, 10.0), "Z": (5.0, 10.0)}
TL = TargetList(R, D, {"A": 0.6, "B": 0.4}, reasons=("12-1 momentum",))
NEWS = {"A": [{"id": "n1", "kind": "news", "scope": "ticker", "source": "wire", "published_at": "2026-11-16T21:00Z",
               "headline": "A files 8-K", "url": "http://x", "n_outlets": 3, "triage": {"x": 1}}]}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the pre-open desk tests must not touch the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def run_desk(d: PreopenDesk) -> dict:
    d.submit(R, D, PLANNED, TL, HELD, NOW, 1)
    job = d._jobs[(R, str(D))]
    if job.thread is not None:
        job.thread.join(5)
    rec = d.collect(R, D)
    assert rec is not None
    return rec


def test_veto_drops_the_buy_downgrade_halves_and_sales_are_untouched():
    rec = run_desk(PreopenDesk(DeterministicReviewer(veto={"A"}, downgrade={"B"}), offline_gateway(), env=ON_ENV,
                               inline=True, needs_key=False))
    assert rec["status"] == "done" and rec["vetoed"] == ["A"] and rec["downgraded"] == ["B"]
    out, block = reviewed_plan(R, dict(PLANNED), HELD, rec)
    got = {t[0]: t[1] for t in out["targets"]}
    assert "A" not in got  # dropped: no order, nothing sold
    assert got["B"] == 1 + (21 - 1) // 2 and got["H"] == 2 and got["Z"] == 0
    assert block["vetoed"] == ["A"] and block["downgraded"] == {"B": [21, 11]}
    assert next(t for t in out["targets"] if t[0] == "B")[3] == "resize;preopen_downgrade"


def test_a_review_runs_in_a_worker_and_the_caller_never_waits():
    gate = threading.Event()

    class Slow(DeterministicReviewer):
        def __call__(self, session, cands):
            gate.wait(5)
            return super().__call__(session, cands)

    d = PreopenDesk(Slow(), offline_gateway(), env=ON_ENV, needs_key=False)
    d.submit(R, D, PLANNED, TL, HELD, NOW, 1)
    d.submit(R, D, PLANNED, TL, HELD, NOW, 1)  # once only
    assert d.pending(R, D) and d.collect(R, D) is None
    gate.set()
    d._jobs[(R, str(D))].thread.join(5)
    rec = d.collect(R, D)
    assert rec["status"] == "done" and not d.pending(R, D) and d.collect(R, D) is None


def test_a_job_with_no_outcome_becomes_a_timeout_with_no_increase():
    t = [0.0]
    gate = threading.Event()

    def hang(session, cands):
        gate.wait(5)

    d = PreopenDesk(hang, offline_gateway(), env=ON_ENV, needs_key=False, clock=lambda: t[0])
    d.submit(R, D, PLANNED, TL, HELD, NOW, 1)
    assert d.collect(R, D) is None
    t[0] = STALE_S + 1
    rec = d.collect(R, D)
    gate.set()
    assert rec["status"] == "timeout"
    out, block = reviewed_plan(R, dict(PLANNED), HELD, rec)
    assert {t[0] for t in out["targets"]} == {"H", "Z"} and sorted(block["vetoed"]) == ["A", "B"]


def test_no_key_skips_the_sessions_buys_with_no_budget_read_and_sales_go_ahead():
    store = FakeStore()
    calls = []
    store.rpc = lambda fn, args: calls.append(fn)
    gw = Gateway(store, None, LLMConfig.load())  # no ANTHROPIC_API_KEY
    rec = run_desk(PreopenDesk(GatewayReviewer(gw), gw, env=ON_ENV, inline=True))
    assert rec["status"] == "no_key" and "ANTHROPIC_API_KEY" in rec["error"] and calls == []
    out, block = reviewed_plan(R, dict(PLANNED), HELD, rec)
    assert {t[0]: t[1] for t in out["targets"]} == {"H": 2, "Z": 0}  # trim and sale still go out
    assert sorted(block["vetoed"]) == ["A", "B"]
    assert status_block(rec, D)["note"].startswith(f"no new buys on {D}")


@pytest.mark.parametrize("day, month, status", [
    (3.0, 0.0, "budget"),  # daily budget used: amber "No new buys on <day>"
    (4.0, 190.0, "no_entries"),  # monthly cap: red
])
def test_exhausted_budget_skips_the_sessions_buys_and_sales_go_ahead(day, month, status):
    store = FakeStore()
    store.llm.update(day=day, month=month)
    llm = FakeLLM({PREOPEN_TOOL["name"]: {}})
    gw = Gateway(store, llm, LLMConfig.load())
    rec = run_desk(PreopenDesk(GatewayReviewer(gw), gw, env=ON_ENV, inline=True))
    assert rec["status"] == status and rec["error"] and llm.calls == []
    out, _ = reviewed_plan(R, dict(PLANNED), HELD, rec)
    assert {t[0]: t[1] for t in out["targets"]} == {"H": 2, "Z": 0}
    ev = event_body(rec)
    assert ev["status"] == status and ev["error"] == rec["error"] and sorted(ev["vetoed"]) == ["A", "B"]


def test_off_leaves_the_rules_plan_unchanged():
    rec = run_desk(PreopenDesk(DeterministicReviewer(veto={"A"}), offline_gateway(), env={}, inline=True,
                               needs_key=False))
    assert rec["status"] == "off"
    assert reviewed_plan(R, dict(PLANNED), HELD, rec)[0]["targets"] == PLANNED["targets"]


def answer_from_prompt(verdict="veto", cite=("n1",)):
    def ans(kw):
        data = json.loads(kw["messages"][0]["content"].split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        return {"session": data["session"], "verdicts": [
            {"candidate_id": c["id"], "verdict": verdict if c["symbol"] == "A" else "allow",
             "flag": "company_news" if c["symbol"] == "A" else "none", "confidence": "high", "reason": "8-K",
             "evidence_ids": list(cite) if c["symbol"] == "A" else []} for c in data["candidates"]]}
    return ans


def test_live_reviewer_through_the_gateway_with_news_evidence_charges_once():
    store = FakeStore()
    llm = FakeLLM({PREOPEN_TOOL["name"]: answer_from_prompt()})
    gw = Gateway(store, llm, LLMConfig.load())
    d = PreopenDesk(GatewayReviewer(gw), gw, env=ON_ENV, inline=True, evidence_fn=lambda syms, as_of: NEWS)
    rec = run_desk(d)
    assert rec["status"] == "done" and rec["vetoed"] == ["A"] and rec["cost_usd"] > 0
    assert len(llm.calls) == 1 and len(store.tables["llm_call"]) == 1
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "A files 8-K" in prompt and "http://x" not in prompt  # url and triage internals are not shown
    shown = next(c for c in rec["candidates"] if c["symbol"] == "A")
    assert shown["features"]["news_7d"] == 1 and shown["evidence"][0]["id"] == "n1"
    assert event_body(rec)["reasons"]["A"].startswith("veto: company_news")


def test_citing_evidence_that_was_not_shown_fails_closed():
    store = FakeStore()
    llm = FakeLLM({PREOPEN_TOOL["name"]: answer_from_prompt(verdict="allow", cite=("n999",))})
    gw = Gateway(store, llm, LLMConfig.load())
    rec = run_desk(PreopenDesk(GatewayReviewer(gw), gw, env=ON_ENV, inline=True,
                               evidence_fn=lambda syms, as_of: NEWS))
    assert rec["status"] == "invalid" and "not shown" in rec["error"]
    out, _ = reviewed_plan(R, dict(PLANNED), HELD, rec)
    assert {t[0] for t in out["targets"]} == {"H", "Z"}  # no increase; sales and trims still go


def test_evidence_failure_shows_no_news_rather_than_inventing_it():
    def boom(syms, as_of):
        raise ConnectionError("news down")

    rec = run_desk(PreopenDesk(DeterministicReviewer(), offline_gateway(), env=ON_ENV, inline=True,
                               needs_key=False, evidence_fn=boom))
    assert rec["status"] == "done" and all("evidence" not in c for c in rec["candidates"])


def test_check_answer_requires_shown_evidence_ids():
    cands = preopen.candidates(D, [TL], {(R, "A"): 4}, {}, evidence=NEWS)
    ok = {"session": str(D), "verdicts": [{"candidate_id": f"{R}:A", "verdict": "veto", "flag": "company_news",
                                           "confidence": "low", "reason": "x", "evidence_ids": ["n1"]}]}
    assert check_answer(ok, D, cands)[0] is not None
    ok["verdicts"][0]["evidence_ids"] = ["n2"]
    assert check_answer(ok, D, cands)[0] is None


def test_nothing_growing_makes_no_call():
    llm = FakeLLM({PREOPEN_TOOL["name"]: {}})
    gw = Gateway(FakeStore(), llm, LLMConfig.load())
    planned = {**PLANNED, "targets": [["H", 2, "target", "trim"]]}
    d = PreopenDesk(GatewayReviewer(gw), gw, env=ON_ENV, inline=True)
    d.submit(R, D, planned, TL, HELD, NOW, 1)
    rec = d.collect(R, D)
    assert rec["status"] == "done" and rec["arms"] == {} and llm.calls == []


def test_make_preopen_is_off_by_default_and_on_only_with_both_flags():
    gw = offline_gateway()
    for env in ({}, {"KAIBOT_ROOM": "on"}, {"KAIBOT_PREOPEN_REVIEW": "on"}, {"KAIBOT_ROOM": "off",
                                                                             "KAIBOT_PREOPEN_REVIEW": "on"}):
        assert make_preopen(gw, None, env=env) is None
    d = make_preopen(gw, None, env=ON_ENV)
    assert isinstance(d, PreopenDesk) and isinstance(d.reviewer, GatewayReviewer) and d.needs_key


def test_evidence_from_the_news_desk_is_ticker_news_and_a_failed_read_is_left_out():
    class Desk:
        def __init__(self):
            self.polled = []

        def poll(self, symbols, lookback=None):
            self.polled.append((tuple(symbols), lookback))

        def evidence_pack(self, symbol, etf, as_of, hours=24):
            if symbol == "BAD":
                return {"items": [], "complete": False}
            return {"items": [{"id": "n1", "scope": "ticker"}, {"id": "n2", "scope": "sector"}], "complete": True,
                    "hours": hours}

    desk = Desk()
    ev = make_preopen_evidence(desk)(["A", "BAD"], NOW)
    assert ev == {"A": [{"id": "n1", "scope": "ticker"}]}
    assert desk.polled and desk.polled[0][1].total_seconds() == 72 * 3600


def test_review_contract_untouched_off():
    reviewed, out, cands = review(D, [TL], {(R, "A"): 4}, {}, DeterministicReviewer(), env={})
    assert out.status == "off" and reviewed == {(R, "A"): 4} and cands == []
