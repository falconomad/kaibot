"""Story S32: no call can go over budget; every call settles; failures fall back to rules-only."""

import threading

from fakes import FakeLLM, FakeStore

from kaibot.agents.gateway import Gateway, LLMConfig, cost_of, from_env

TOOL = {"name": "score", "description": "x",
        "input_schema": {"type": "object", "properties": {"s": {"type": "number"}}}}
CFG = LLMConfig.load()


def gw(store=None, client=None):
    return Gateway(store or FakeStore(), client if client is not None else FakeLLM({"score": {"s": 1}}), CFG)


def test_happy_path_settles_actual_cost():
    g = gw()
    r = g.call("triage", "claude-haiku-5-5", "sys", "hello", TOOL, 300, 10)
    assert r.data == {"s": 1}
    assert abs(r.cost_usd - cost_of(CFG.prices["claude-haiku-5-5"], 800, 150)) < 1e-12
    b = g.store.llm
    assert b["open"] == {} and abs(b["day"] - r.cost_usd) < 1e-12


def test_forced_tool_and_cached_system_prompt():
    llm = FakeLLM({"score": {"s": 1}})
    gw(client=llm).call("triage", "claude-haiku-5-5", "sys", "u", TOOL, 300, 10)
    kw = llm.calls[0]
    assert kw["tool_choice"] == {"type": "tool", "name": "score"} and kw["timeout"] == 10
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_refused_when_the_day_is_spent():
    st = FakeStore()
    st.llm["day"] = CFG.daily_soft_usd - 0.0001
    llm = FakeLLM({"score": {"s": 1}})
    assert gw(st, llm).call("room", "claude-sonnet-5-5", "s" * 4000, "u" * 4000, TOOL, 700, 15) is None
    assert llm.calls == []  # never reached the API


def test_failure_still_settles_and_returns_none():
    st = FakeStore()
    llm = FakeLLM({"score": {"s": 1}}, fail={"score"})
    assert gw(st, llm).call("room", "claude-sonnet-5-5", "s", "u", TOOL, 700, 15) is None
    assert st.llm["open"] == {} and st.tables["llm_call"][0]["error"].startswith("TimeoutError")


def test_missing_tool_block_is_a_failure():
    llm = FakeLLM({"score": {"s": 1}})
    real = llm.create

    def no_tool(**kw):
        r = real(**kw)
        r.content = []
        return r

    llm.create = no_tool
    assert gw(client=llm).call("triage", "claude-haiku-5-5", "s", "u", TOOL, 300, 10) is None


def test_concurrent_calls_never_exceed_the_cap():
    """50 threads race for a budget that fits only a few worst-case reservations."""
    st = FakeStore()
    lock = threading.Lock()
    real_rpc = st.rpc
    st.rpc = lambda fn, a: (lock.acquire(), real_rpc(fn, a), lock.release())[1]  # the DB row lock
    g = gw(st, FakeLLM({"score": {"s": 1}}, usage=(30_000, 700, 0, 0)))
    results = []
    th = [threading.Thread(target=lambda: results.append(
        g.call("room", "claude-sonnet-5-5", "x" * 90_000, "u", TOOL, 700, 15))) for _ in range(50)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert st.llm["day"] <= CFG.daily_soft_usd + 1e-9
    assert sum(r is not None for r in results) < 50


def test_disabled_without_key_or_flag():
    assert not from_env(FakeStore(), {}).enabled
    assert not from_env(FakeStore(), {"ANTHROPIC_API_KEY": "sk-x"}).enabled  # KAIBOT_ROOM not on
    assert gw(client=None).client is not None  # sanity: helper builds a fake by default
    assert Gateway(FakeStore(), None, CFG).call("t", "claude-haiku-5-5", "s", "u", TOOL, 10, 1) is None


def test_haiku_long_prompt_refused():
    llm = FakeLLM({"score": {"s": 1}})
    assert gw(client=llm).call("triage", "claude-haiku-5-5", "x" * 400_000, "u", TOOL, 300, 10) is None
    assert llm.calls == []


def test_budget_state_fails_closed():
    st = FakeStore()
    st.rpc = lambda *a: (_ for _ in ()).throw(RuntimeError("db down"))
    assert gw(st).state()["state"] == "rules_only"
