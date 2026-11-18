"""#309 (Stage 1, the stakeholder's $200 of API credit): the LLM caps are $1 a day, $20 a month alert, $25 a month
hard and $150 in all, tighten-only. The lifetime total is the sum of `kaibot.budget_day` (one row per ET day).
Offline: FakeStore mirrors the budget RPCs and the ledger; FakeLLM stands in for the API."""

import dataclasses
import re
from datetime import date
from pathlib import Path

import pytest
import yaml
from fakes import FakeLLM, FakeStore

from kaibot.agents.budget import BudgetGateway, BudgetState, ModelPrice
from kaibot.agents.gateway import CEILINGS, Gateway, LLMConfig

ROOT = Path(__file__).resolve().parents[1]
CFG = LLMConfig.load()
TOOL = {"name": "score", "description": "x",
        "input_schema": {"type": "object", "properties": {"s": {"type": "number"}}}}
HAIKU = "claude-haiku-5-5"


def gw(store=None, cfg=CFG, **kw):
    return Gateway(store or FakeStore(), FakeLLM({"score": {"s": 1}}), cfg, **kw)


def spy(store):
    seen = []
    real = store.rpc
    store.rpc = lambda fn, args: (seen.append((fn, dict(args))), real(fn, args))[1]
    return seen


# ---------- the caps ----------

def test_the_configured_caps_fit_the_credit():
    raw = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text())["budget"]
    assert (raw["daily_soft_usd"], raw["monthly_alert_usd"], raw["monthly_hard_usd"], raw["lifetime_hard_usd"]) == (
        1.0, 20.0, 25.0, 150.0)
    assert (CFG.daily_soft_usd, CFG.monthly_alert_usd, CFG.monthly_hard_usd, CFG.lifetime_hard_usd) == (
        1.0, 20.0, 25.0, 150.0)
    assert CFG.limits["preopen"]["daily_usd"] == 1.0
    assert CFG.lifetime_hard_usd < 200.0  # inside the credit, with room for a month's overrun


def test_the_in_memory_budget_has_the_same_caps():
    b = BudgetGateway({})
    assert (b.daily_soft_usd, b.monthly_alert_usd, b.monthly_hard_usd, b.lifetime_hard_usd) == (
        CFG.daily_soft_usd, CFG.monthly_alert_usd, CFG.monthly_hard_usd, CFG.lifetime_hard_usd)


def test_the_config_can_only_tighten(tmp_path):
    raw = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text())
    loose = dict(raw, budget={**raw["budget"], "daily_soft_usd": 3.0, "monthly_alert_usd": 150.0,
                              "monthly_hard_usd": 190.0, "lifetime_hard_usd": 1000.0})
    (tmp_path / "loose.yaml").write_text(yaml.safe_dump(loose))
    c = LLMConfig.load(tmp_path / "loose.yaml")
    assert (c.daily_soft_usd, c.monthly_alert_usd, c.monthly_hard_usd, c.lifetime_hard_usd) == (
        CEILINGS["daily_soft_usd"], CEILINGS["monthly_alert_usd"], CEILINGS["monthly_hard_usd"],
        CEILINGS["lifetime_hard_usd"])
    tight = dict(raw, budget={**raw["budget"], "daily_soft_usd": 0.5, "lifetime_hard_usd": 40.0})
    (tmp_path / "tight.yaml").write_text(yaml.safe_dump(tight))
    c = LLMConfig.load(tmp_path / "tight.yaml")
    assert (c.daily_soft_usd, c.lifetime_hard_usd) == (0.5, 40.0)
    no_life = dict(raw, budget={k: v for k, v in raw["budget"].items() if k != "lifetime_hard_usd"})
    (tmp_path / "old.yaml").write_text(yaml.safe_dump(no_life))
    assert LLMConfig.load(tmp_path / "old.yaml").lifetime_hard_usd == 150.0  # missing key: the ceiling, not "none"
    # built in code, too (dataclasses.replace runs __post_init__)
    assert dataclasses.replace(CFG, monthly_hard_usd=500.0).monthly_hard_usd == 25.0
    assert dataclasses.replace(CFG, daily_soft_usd=float("nan")).daily_soft_usd == 0.0


# ---------- the lifetime cap on the live (Postgres) ledger ----------

def test_the_reservation_gets_the_monthly_cap_cut_by_the_lifetime_left():
    st = FakeStore()
    st.llm["before"] = 140.0  # $140 spent in earlier months: $10 left in all
    seen = spy(st)
    assert gw(st).call("triage", HAIKU, "s", "u", TOOL, 300, 10) is not None
    res = [a for fn, a in seen if fn == "llm_reserve"]
    assert res and res[0]["p_monthly_hard"] == pytest.approx(10.0) and res[0]["p_daily_soft"] == 1.0
    st2 = FakeStore()
    seen2 = spy(st2)
    gw(st2).call("triage", HAIKU, "s", "u", TOOL, 300, 10)
    assert [a for fn, a in seen2 if fn == "llm_reserve"][0]["p_monthly_hard"] == 25.0  # far from the lifetime cap


def test_no_call_once_the_lifetime_cap_is_reached():
    st = FakeStore()
    st.llm.update(before=145.0, month=5.0, day=0.0)  # $150 in all, though today and this month are under their caps
    llm = FakeLLM({"score": {"s": 1}})
    g = Gateway(st, llm, CFG)
    report: dict = {}
    assert g.call("triage", HAIKU, "s", "u", TOOL, 300, 10, report=report) is None
    assert report["reason"] == "refused" and llm.calls == [] and "llm_call" not in st.tables
    s = g.state()
    assert s["state"] == "no_entries" and s["lifetime"] == pytest.approx(150.0) and s["lifetime_cap"] == 150.0


def test_a_call_that_would_cross_the_lifetime_cap_is_refused_by_the_reservation():
    st = FakeStore()
    st.llm.update(before=149.99999, month=0.0)
    llm = FakeLLM({"score": {"s": 1}})
    report: dict = {}
    assert Gateway(st, llm, CFG).call("room", "claude-sonnet-5-5", "s" * 3000, "u", TOOL, 700, 15,
                                      report=report) is None
    assert report["reason"] == "refused" and llm.calls == []


def test_spend_counts_towards_the_lifetime_total():
    st = FakeStore()
    st.llm["before"] = 12.0
    g = gw(st)
    r = g.call("triage", HAIKU, "s", "u", TOOL, 300, 10)
    s = g.state()
    assert s["lifetime"] == pytest.approx(12.0 + r.cost_usd) and s["state"] == "ok"


def test_an_unreadable_ledger_refuses_the_call_and_reads_rules_only():
    st = FakeStore()

    def down(table, params):
        raise ConnectionError("supabase unreachable")

    st.select = down
    llm = FakeLLM({"score": {"s": 1}})
    g = Gateway(st, llm, CFG)
    report: dict = {}
    assert g.call("triage", HAIKU, "s", "u", TOOL, 300, 10, report=report) is None
    assert report["reason"] == "reserve_failed" and llm.calls == [] and "llm_call" not in st.tables
    assert g.state()["state"] == "rules_only"


def test_a_ledger_read_that_may_be_cut_is_refused():
    st = FakeStore()
    st.tables["budget_day"] = [{"day": f"2000-01-{i % 28 + 1:02d}", "spent_usd": 0.01} for i in range(1000)]
    report: dict = {}
    assert gw(st).call("triage", HAIKU, "s", "u", TOOL, 300, 10, report=report) is None
    assert report["reason"] == "reserve_failed"


def test_the_month_split_uses_the_et_day():
    st = FakeStore()
    st.tables["budget_day"] = [{"day": "2026-10-31", "spent_usd": 100.0}, {"day": "2026-11-02", "spent_usd": 3.0}]
    life = Gateway(st, None, CFG, today=lambda: date(2026, 11, 18)).lifetime()
    assert life == {"before_month": 100.0, "lifetime": 103.0, "monthly_cap": 25.0}
    life = Gateway(st, None, CFG, today=lambda: date(2026, 12, 1)).lifetime()
    assert life["before_month"] == 103.0 and life["monthly_cap"] == 25.0
    st.tables["budget_day"].append({"day": "2026-11-20", "spent_usd": 40.0})
    assert Gateway(st, None, CFG, today=lambda: date(2026, 12, 1)).lifetime()["monthly_cap"] == pytest.approx(7.0)


# ---------- the in-memory model ----------

PRICES = {"haiku": ModelPrice(0.10, 0.50)}


def test_in_memory_lifetime_cap_and_it_never_resets():
    b = BudgetGateway(PRICES, spent_lifetime=149.9999)
    assert b.reserve("haiku", 1000, 300) is None and b.state() is BudgetState.OK  # refused: it would cross $150
    assert BudgetGateway(PRICES, spent_lifetime=150.0).state() is BudgetState.NO_ENTRIES
    b = BudgetGateway(PRICES, spent_lifetime=10.0)
    r = b.reserve("haiku", 1000, 300)
    b.settle(r, 0.5)
    b.new_month()
    assert b.spent_month == 0.0 and b.spent_lifetime == pytest.approx(10.5)
    assert BudgetGateway(PRICES, spent_month=3.0).spent_lifetime == 3.0  # a month's spend is part of the total


# ---------- the dashboard shows the same caps ----------

def test_the_dashboard_function_shows_the_new_caps():
    sql = (ROOT / "migrations" / "0025_llm_budget_caps.sql").read_text()
    body = sql.split("create or replace function public.kb_llm_budget()", 1)[1]
    assert "kaibot.llm_budget_state(1.0, " in body
    caps = re.search(r"'caps', jsonb_build_object\(([^)]*)\)", body).group(1)
    got = dict(re.findall(r"'(\w+)', ([\d.]+)", caps))
    assert {k: float(v) for k, v in got.items()} == {"daily_soft": 1.0, "monthly_alert": 20.0, "monthly_hard": 25.0,
                                                     "lifetime_hard": 150.0}
