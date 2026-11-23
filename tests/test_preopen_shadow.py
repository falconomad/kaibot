"""Issue #310: the shadow pre-open review (agents/preopen_shadow.py) and its comparison script. An observer: it is
off by default, needs only KAIBOT_PREOPEN=shadow (never KAIBOT_ROOM), records verdicts, returns nothing, skips when
the budget gateway refuses, and only logs on errors. Offline: FakeStore, FakeLLM, stub reviewers."""

import json
import socket
from datetime import date, datetime

import pytest
from fakes import FakeLLM, FakeStore

from kaibot.agents import preopen_shadow as ps
from kaibot.agents import shadow_compare as sc
from kaibot.agents.gateway import Gateway, LLMConfig, from_env, shadow_from_env
from kaibot.agents.preopen import PREOPEN_TOOL, GatewayReviewer
from kaibot.agents.preopen_daily import DeterministicReviewer
from kaibot.engine import m3glue
from kaibot.strategies.base import TargetList

CFG = LLMConfig.load()
D = date(2026, 11, 18)
R = "trend_momentum"
PLANNED = {"run_id": 1, "strategy": R, "session": str(D), "decided": "2026-11-17",
           "targets": [["A", 10, "buy", None], ["B", 6, "buy", None], ["H", 2, "trim", "rank"], ["Z", 0, "sell", "x"]]}
HELD = {"B": (3.0, 10.0), "H": (5.0, 20.0), "Z": (4.0, 30.0)}
TL = TargetList(R, date(2026, 11, 17), {"A": 0.5, "B": 0.3, "H": 0.2}, reasons=("12-1 momentum",))
NOW = datetime(2026, 11, 18, 14, 31)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the shadow tests must not touch the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def model_answer(**over):
    def ans(kw):
        data = json.loads(kw["messages"][0]["content"].split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        return {"session": data["session"],
                "verdicts": [{"candidate_id": c["id"], "verdict": over.get(c["symbol"], "allow"),
                              "flag": "company_news" if c["symbol"] in over else "none", "confidence": "medium",
                              "reason": f"why {c['symbol']}"} for c in data["candidates"]]}
    return ans


def run(desk, planned=PLANNED, held=HELD):
    desk.observe(R, D, planned, TL, held, NOW, 1)
    return desk.collect(R, D)


def live(store=None, **over):
    store = store or FakeStore()
    llm = FakeLLM({PREOPEN_TOOL["name"]: model_answer(**over)})
    gw = Gateway(store, llm, CFG)
    return store, llm, ps.ShadowDesk(GatewayReviewer(gw), gw, inline=True, masked=False)  # M12 3.9: raw arm alone


# ---------- switches ----------

def test_off_by_default_and_independent_of_the_room():
    assert not ps.enabled({}) and not ps.enabled({"KAIBOT_ROOM": "on", "KAIBOT_PREOPEN_REVIEW": "on"})
    assert ps.enabled({"KAIBOT_PREOPEN": "shadow"}) and ps.enabled({"KAIBOT_PREOPEN": " Shadow "})
    assert not ps.enabled({"KAIBOT_PREOPEN": "on"})  # only the word "shadow" switches it on
    assert m3glue.make_preopen_shadow(FakeStore(), None, env={}) is None
    assert m3glue.make_preopen_shadow(FakeStore(), None, env={"KAIBOT_ROOM": "on"}) is None
    desk = m3glue.make_preopen_shadow(FakeStore(), None, env={"KAIBOT_PREOPEN": "shadow"})
    assert isinstance(desk, ps.ShadowDesk) and not desk.gateway.enabled  # no key: built, spends nothing
    # the shadow gateway never needs the room; the room's gateway never comes on with the shadow switch
    assert from_env(FakeStore(), {"ANTHROPIC_API_KEY": "k", "KAIBOT_PREOPEN": "shadow"}).enabled is False
    assert shadow_from_env(FakeStore(), {"ANTHROPIC_API_KEY": "k", "KAIBOT_ROOM": "on"}).enabled is False
    assert shadow_from_env(FakeStore(), {"KAIBOT_PREOPEN": "shadow"}).enabled is False


def test_the_shadow_switch_does_not_turn_the_acting_review_on():
    from kaibot.agents import preopen

    env = {"KAIBOT_PREOPEN": "shadow", "ANTHROPIC_API_KEY": "k"}
    assert not preopen.enabled(env) and m3glue.make_preopen(None, None, env=env) is None


# ---------- what it records ----------

def test_records_verdicts_with_reasons_and_returns_nothing():
    store, llm, desk = live(A="veto")
    planned = json.loads(json.dumps(PLANNED))
    held = dict(HELD)
    assert desk.observe(R, D, planned, TL, held, NOW, 1) is None
    rec = desk.collect(R, D)
    assert planned == PLANNED and held == HELD  # its inputs are untouched
    assert rec["status"] == "done" and rec["acted"] is False and rec["mode"] == "shadow"
    assert rec["verdicts"] == {f"{R}:A": "veto", f"{R}:B": "allow"}  # only the buys; trims and sales unseen
    assert rec["would_have"] == {"vetoed": ["A"], "downgraded": [], "allowed": ["B"]}
    assert rec["buys"] == {f"{R}:A": {"current": 0.0, "rules": 10}, f"{R}:B": {"current": 3.0, "rules": 6}}
    ev = ps.event_body(rec)
    assert ev["would_veto"] == ["A"] and ev["reasons"]["A"].startswith("veto: company_news: why A")
    assert ps.status_block(rec, D)["note"] == "shadow review (observer, no effect): would veto A"
    assert len(llm.calls) == 1 and store.tables["llm_call"][0]["purpose"] == "preopen"
    assert desk.collect(R, D) is None  # once


def test_one_review_per_rule_and_session():
    store, llm, desk = live()
    for _ in range(3):
        desk.observe(R, D, PLANNED, TL, HELD, NOW, 1)
    assert len(llm.calls) == 1


def test_no_buys_no_call():
    store, llm, desk = live()
    rec = run(desk, {**PLANNED, "targets": [["Z", 0, "sell", "x"]]})
    assert rec["status"] == "done" and rec["verdicts"] == {} and llm.calls == [] and "llm_call" not in store.tables


def test_skipped_when_the_budget_gateway_refuses():
    store = FakeStore()
    store.llm["day"] = CFG.daily_soft_usd  # the $1 day is spent
    _, llm, desk = live(store)
    rec = run(desk)
    assert rec["status"] == "skipped_budget" and llm.calls == [] and "llm_call" not in store.tables
    store = FakeStore()
    store.llm["day"] = CFG.daily_soft_usd - 0.0001  # state ok, the reservation does not fit
    _, llm, desk = live(store)
    assert run(desk)["status"] == "skipped_budget" and llm.calls == []
    store = FakeStore()
    store.llm["before"] = CFG.lifetime_hard_usd  # #309: the lifetime cap
    _, llm, desk = live(store)
    assert run(desk)["status"] == "skipped_budget" and llm.calls == []


def test_no_key_is_recorded_and_spends_nothing():
    store = FakeStore()
    seen = []
    store.rpc = lambda fn, args: seen.append(fn)
    gw = Gateway(store, None, CFG)
    rec = run(ps.ShadowDesk(GatewayReviewer(gw), gw, inline=True))
    assert rec["status"] == "skipped_no_key" and seen == []


def test_errors_only_log(caplog):
    class Boom:
        def __call__(self, *a):
            raise RuntimeError("down")

    store, _, _ = live()
    desk = ps.ShadowDesk(Boom(), Gateway(store, FakeLLM({}), CFG), inline=True)
    rec = run(desk)
    assert rec["status"] == "failed" and "down" in rec["error"]
    desk = ps.ShadowDesk(DeterministicReviewer(), Gateway(store, FakeLLM({}), CFG), inline=True)
    desk.observe(R, D, {"targets": "not a list"}, TL, HELD, NOW, 1)  # a broken input: logged, never raised


def test_a_broken_answer_is_recorded_as_failed():
    store = FakeStore()
    gw = Gateway(store, FakeLLM({PREOPEN_TOOL["name"]: {"session": "1999-01-01", "verdicts": []}}), CFG)
    assert run(ps.ShadowDesk(GatewayReviewer(gw), gw, inline=True))["status"] == "failed"


def test_a_stale_job_is_a_timeout():
    t = [0.0]
    desk = ps.ShadowDesk(DeterministicReviewer(), None, clock=lambda: t[0], needs_key=False)

    class Never:
        def start(self):
            pass

    import threading
    real = threading.Thread
    threading.Thread = lambda *a, **k: Never()
    try:
        desk.observe(R, D, PLANNED, TL, HELD, NOW, 1)
    finally:
        threading.Thread = real
    assert desk.collect(R, D) is None and desk.finished() == []
    t[0] = ps.STALE_S + 1
    assert desk.finished() == [(R, str(D))] and desk.collect(R, D)["status"] == "failed"


# ---------- the comparison script ----------

def test_outcomes_join_verdicts_with_the_buys_actual_results():
    shadow = [{"strategy": R, "session": "2026-11-02", "status": "done",
               "verdicts": {f"{R}:A": "veto", f"{R}:B": "allow", f"{R}:C": "downgrade"},
               "answer": {"verdicts": [{"candidate_id": f"{R}:A", "verdict": "veto", "flag": "earnings_soon",
                                        "reason": "earnings tomorrow"}]}},
              {"strategy": R, "session": "2026-11-03", "status": "skipped_budget", "verdicts": {}}]
    fills = [{"strategy": R, "symbol": "A", "side": "buy", "px": 100.0, "session": "2026-11-02"},
             {"strategy": R, "symbol": "B", "side": "buy", "px": 50.0, "session": "2026-11-02"},
             {"strategy": R, "symbol": "C", "side": "buy", "px": 20.0, "session": "2026-11-02"},
             {"strategy": R, "symbol": "B", "side": "sell", "px": 55.0, "session": "2026-11-04"}]
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    marks = [{"session": d, "prices": {f"{R}|A": 100.0 - 2 * i, f"{R}|C": 20.0}} for i, d in enumerate(days)]
    rows = {r["symbol"]: r for r in sc.outcomes(shadow, fills, marks, horizon=2)}
    assert rows["A"]["ret"] == pytest.approx(96.0 / 100.0 - 1) and rows["A"]["reason"].startswith("veto: earnings")
    assert rows["B"]["ret"] == pytest.approx(0.1) and rows["B"]["exit"] == "sold 2026-11-04"
    assert rows["C"]["ret"] == 0.0
    assert sc.outcomes(shadow, fills, marks, horizon=5)[0]["ret"] is None  # not reached yet
    s = sc.summary(list(rows.values()))
    assert s["veto"]["share_lost"] == 1.0 and s["allow"]["mean_ret"] == pytest.approx(0.1)
    text = sc.report(list(rows.values()), 2)
    assert "| veto | 1 | 1 |" in text and "Nothing acted on these verdicts" in text
