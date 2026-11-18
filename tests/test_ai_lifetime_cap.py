"""#319: one $150 lifetime cap across every AI dollar: the engine's LLM calls (kaibot.budget_day) and the dashboard's
stock summaries (kaibot.summary_call, #312). The engine side, offline: FakeStore mirrors migration 0026's
kaibot.ai_spend and kaibot.llm_reserve_lifetime (and a database without them); FakeLLM stands in for the API.
The SQL side runs on the scratch Postgres: tests/test_ai_lifetime_cap_sql.py."""

import pytest
from fakes import FakeLLM, FakeStore

from kaibot.agents.gateway import Gateway, LLMConfig

CFG = LLMConfig.load()
TOOL = {"name": "score", "description": "x",
        "input_schema": {"type": "object", "properties": {"s": {"type": "number"}}}}
HAIKU = "claude-haiku-5-5"


def spy(store):
    seen = []
    real = store.rpc
    store.rpc = lambda fn, args: (seen.append((fn, dict(args))), real(fn, args))[1]
    return seen


def call(store, report=None):
    llm = FakeLLM({"score": {"s": 1}})
    r = Gateway(store, llm, CFG).call("triage", HAIKU, "s", "u", TOOL, 300, 10, report=report)
    return r, llm


@pytest.mark.parametrize("pre", [False, True])  # with migration 0026, and on a database without it
def test_the_summaries_count_towards_the_engines_lifetime_cap(pre):
    st = FakeStore()
    st.pre_0026 = pre
    st.llm.update(before=140.0, summaries=10.0)  # $150 in all: nothing is left
    report: dict = {}
    r, llm = call(st, report)
    assert r is None and report["reason"] == "refused" and llm.calls == [] and "llm_call" not in st.tables
    s = Gateway(st, None, CFG).state()
    assert s["state"] == "no_entries" and s["lifetime"] == pytest.approx(150.0)
    assert s["lifetime_summaries"] == pytest.approx(10.0)


@pytest.mark.parametrize("pre", [False, True])
def test_the_monthly_cap_handed_to_the_reservation_takes_off_the_summaries(pre):
    st = FakeStore()
    st.pre_0026 = pre
    st.llm.update(before=120.0, summaries=7.5)  # $22.50 left in all, under this month's $25
    seen = spy(st)
    r, _ = call(st)
    assert r is not None
    res = [(fn, a) for fn, a in seen if fn in ("llm_reserve", "llm_reserve_lifetime")]
    fn, a = res[0]
    assert a["p_monthly_hard"] == pytest.approx(22.5) and a["p_daily_soft"] == 1.0
    if pre:
        assert fn == "llm_reserve"  # the old function, with the combined headroom as its monthly cap
    else:
        assert fn == "llm_reserve_lifetime" and a["p_lifetime_hard"] == 150.0  # held inside the row lock too


def test_the_database_holds_the_lifetime_cap_inside_the_lock():
    st = FakeStore()
    st.llm.update(before=100.0, summaries=0.0)
    r, _ = call(st)
    assert r is not None
    st.llm["summaries"] = 49.99999  # a summary reserved after the engine's read: the database still refuses
    real = st._ai_spend
    reads = []

    def stale():  # the gateway's own read happened before the summary (a race); the RPC reads now
        reads.append(1)
        out = real()
        if len(reads) == 1:
            out = {**out, "summaries": 0.0, "total": out["total"] - 49.99999}
        return out

    st._ai_spend = stale
    report: dict = {}
    r, llm = call(st, report)
    assert r is None and report["reason"] == "refused" and llm.calls == []


def test_an_unreadable_summary_ledger_refuses_the_call():
    st = FakeStore()
    st.pre_0026 = True
    real = st.select

    def sel(table, params):
        if table == "summary_call":
            raise ConnectionError("down")
        return real(table, params)

    st.select = sel
    report: dict = {}
    r, llm = call(st, report)
    assert r is None and report["reason"] == "reserve_failed" and llm.calls == []


def test_a_summary_ledger_that_may_be_cut_is_refused():
    st = FakeStore()
    st.pre_0026 = True
    st.tables["summary_call"] = [{"day": "2026-11-01", "actual_usd": 0.001, "reserved_usd": 0.001}] * 1000
    report: dict = {}
    assert call(st, report)[0] is None and report["reason"] == "reserve_failed"


def test_the_summaries_count_at_their_reservation_until_settled():
    st = FakeStore()
    st.pre_0026 = True
    st.tables["summary_call"] = [{"day": "2026-11-01", "actual_usd": None, "reserved_usd": 0.02},
                                 {"day": "2026-11-02", "actual_usd": 0.004, "reserved_usd": 0.02}]
    life = Gateway(st, None, CFG).lifetime()
    assert life["summaries"] == pytest.approx(0.024)


def test_migration_0026_is_additive_and_engine_functions_are_service_role_only():
    import re
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "migrations" / "0026_ai_lifetime_cap.sql").read_text()
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert not re.search(r"\b(drop|truncate|alter\s+table)\b", code, re.I)
    assert not re.search(r"\bdelete\s+from\b", code, re.I)
    assert "lifetime_cap constant numeric := 150.0" in code and "'lifetime_cap'" in code
    assert code.count("for update") == 2  # both reservation paths take today's budget_day row lock
    assert re.search(r"grant execute on function kaibot\.ai_lifetime_spent\(integer\), kaibot\.ai_spend\(\),\s+"
                     r"kaibot\.llm_reserve_lifetime\([^)]*\) to service_role;", code)
