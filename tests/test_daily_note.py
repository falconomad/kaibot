"""Issue #311: the daily report in plain words (agents/daily_note.py). One short paragraph after the close, written
by the cheap model only from the engine's own records; facts only, it decides nothing. Offline: FakeStore, FakeLLM
with stubbed answers, no network."""

import json
import socket
from datetime import UTC, date, datetime

import pytest
from fakes import FakeLLM, FakeStore

from kaibot.agents import daily_note as dn
from kaibot.agents.gateway import Gateway, LLMConfig, from_env, note_from_env
from kaibot.agents.room import FORBIDDEN_FIELDS, _field_names
from kaibot.engine import m3glue

CFG = LLMConfig.load()
D = date(2026, 11, 18)
R = "trend_momentum"
ENTRIES = {
    "md_send": [{"strategy": R, "session": str(D),
                 "targets": [["AAA", 12, "buy", "rank 3"], ["BBB", 0, "sell", "rank fell"], ["CCC", 30, "resize", 30]],
                 "skipped": [["DDD", "sector cap"]], "ca_skipped": [["EEE", "spin_off", "2026-12-01", "x"]]},
                {"strategy": R, "session": "2026-11-17", "targets": [["OLD", 5, "buy", "x"]]}],
    "md_sent": [{"strategy": R, "session": str(D), "skipped": [["FFF", "entries blocked: reconciliation"]]}],
    "fill": [{"strategy": R, "session": str(D), "symbol": "AAA", "side": "buy", "qty": 12.0, "px": 101.25,
              "purpose": "buy"},
             {"strategy": R, "session": str(D), "symbol": "BBB", "side": "sell", "qty": 8.0, "px": 55.5,
              "purpose": "sell"},
             {"strategy": R, "session": "2026-11-17", "symbol": "OLD", "side": "buy", "qty": 5.0, "px": 9.0}],
}
HELD = {R: {"AAA": (12.0, 101.0), "CCC": (30.0, 40.0)}}
BOOK = {"equity": 100512.37, "cash": 4521.1}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the daily-note tests must not touch the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def F():
    return dn.facts(D, ENTRIES, HELD, BOOK)


def gw(answer, store=None, **kw):
    store = store or FakeStore()
    llm = FakeLLM({dn.NOTE_TOOL["name"]: answer}, **kw)
    return store, llm, Gateway(store, llm, CFG)


GOOD = ("Today kaibo bought 12 AAA at $101.25 because it ranked third, and sold 8 BBB at $55.50 after its rank fell. "
        "It did not buy DDD (sector cap), EEE (an announced spin-off) or FFF (entries blocked). "
        "It now holds 2 stocks; equity at the last close was $100,512.37 with $4,521.10 in cash.")


def test_facts_come_only_from_the_days_records():
    f = F()
    r = f["rules"][R]
    assert f["session"] == str(D)
    assert r["bought"] == [{"symbol": "AAA", "shares": 12, "price": 101.25}]
    assert r["sold"] == [{"symbol": "BBB", "shares": 8, "price": 55.5}]
    assert r["not_bought_by_the_builder"] == [["DDD", "sector cap"]]
    assert r["not_bought_corporate_action"] == [{"symbol": "EEE", "action": "spin_off", "on": "2026-12-01"}]
    assert r["orders_not_sent"] == [{"symbol": "FFF", "why": "entries blocked: reconciliation"}]
    assert [x["symbol"] for x in r["holding_after_the_close"]] == ["AAA", "CCC"]
    assert "OLD" not in json.dumps(f)  # another day's records are not today's
    assert f["book"] == {"equity_at_last_close_usd": 100512.37, "cash_usd": 4521.1}


def test_the_model_writes_one_paragraph_from_the_facts_only():
    store, llm, g = gw({"paragraph": GOOD})
    rec = dn.write(D, F(), g)
    assert rec["source"] == "model" and rec["text"] == GOOD and rec["model"] == "claude-haiku-5-5"
    kw = llm.calls[0]
    assert kw["model"] == CFG.models["note"] == "claude-haiku-5-5"
    data = json.loads(kw["messages"][0]["content"].split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
    assert data == json.loads(json.dumps(F()))  # the model sees the facts and nothing else
    assert kw["max_tokens"] == CFG.limits["note"]["max_tokens"]
    call = store.tables["llm_call"][0]
    assert call["purpose"] == "daily_note" and rec["cost_usd"] < 0.01  # about a cent a day at most


def test_a_paragraph_with_a_number_not_in_the_facts_is_replaced_by_the_template(caplog):
    _, _, g = gw({"paragraph": GOOD + " Tomorrow it expects a 7.5% gain."})
    rec = dn.write(D, F(), g)
    assert rec["source"] == "template" and "7.5" in rec["why"] and rec["text"] == dn.plain(F())
    assert dn.unknown_numbers(GOOD, F()) == []


def test_no_key_budget_refused_or_a_broken_answer_give_the_template():
    store = FakeStore()
    seen = []
    store.rpc = lambda fn, a: seen.append(fn)
    rec = dn.write(D, F(), Gateway(store, None, CFG))
    assert rec["source"] == "template" and seen == [] and "ANTHROPIC_API_KEY" in rec["why"]
    st = FakeStore()
    st.llm["day"] = CFG.daily_soft_usd
    _, llm, g = gw({"paragraph": GOOD}, store=st)
    rec = dn.write(D, F(), g)
    assert rec["source"] == "template" and "refused" in rec["why"] and llm.calls == []
    _, _, g = gw({"paragraph": "x", "advice": "buy"})
    assert dn.write(D, F(), g)["source"] == "template"
    _, _, g = gw({"paragraph": GOOD}, fail={dn.NOTE_TOOL["name"]})
    assert dn.write(D, F(), g)["source"] == "template"


def test_the_template_states_the_facts():
    t = dn.plain(F())
    assert "bought 12 AAA" in t and "sold 8 BBB" in t and "Not bought: FFF, EEE" in t and "Holding 2 stocks" in t
    assert dn.unknown_numbers(t, F()) == []
    empty = dn.facts(D, {}, {R: {}}, {"equity": None, "cash": None})
    assert dn.plain(empty) == f"{R}: no trades today. Holding nothing."


def test_the_tool_decides_nothing():
    assert not (_field_names(dn.NOTE_TOOL["input_schema"]) & FORBIDDEN_FIELDS)
    assert set(dn.NOTE_TOOL["input_schema"]["properties"]) == {"paragraph"}


def test_off_by_default_and_independent_of_the_room():
    assert not dn.enabled({}) and dn.enabled({"KAIBOT_DAILY_NOTE": "on"})
    assert m3glue.make_daily_note(FakeStore(), env={}) is None
    assert m3glue.make_daily_note(FakeStore(), env={"KAIBOT_ROOM": "on"}) is None
    desk = m3glue.make_daily_note(FakeStore(), env={"KAIBOT_DAILY_NOTE": "on"})
    assert isinstance(desk, dn.NoteDesk) and not desk.gateway.enabled
    assert note_from_env(FakeStore(), {"ANTHROPIC_API_KEY": "k", "KAIBOT_ROOM": "on"}).enabled is False
    assert from_env(FakeStore(), {"ANTHROPIC_API_KEY": "k", "KAIBOT_DAILY_NOTE": "on"}).enabled is False


class Live:
    """The part of LiveDaily the desk reads."""

    class _R:
        name = R

    class _A:
        def held(self, rule):
            return HELD[rule]

        def equity(self):
            return BOOK["equity"]

        def cash(self):
            return BOOK["cash"]

    def __init__(self, now, entries):
        self.now, self.e, self.rules, self.account = (lambda: now), entries, [self._R()], self._A()

    def _entries(self, kind):
        return self.e.get(kind, [])


def test_the_desk_writes_once_after_1700_et():
    _, llm, g = gw({"paragraph": GOOD})
    desk = dn.NoteDesk(g, inline=True)
    before = Live(datetime(2026, 11, 18, 21, 30, tzinfo=UTC), ENTRIES)  # 16:30 ET
    assert desk.tick(before) == [] and llm.calls == []
    after = Live(datetime(2026, 11, 18, 22, 5, tzinfo=UTC), ENTRIES)  # 17:05 ET
    recs = desk.tick(after)
    assert len(recs) == 1 and recs[0]["source"] == "model" and recs[0]["session"] == str(D)
    assert desk.tick(after) == [] and len(llm.calls) == 1  # once a day
    done = Live(datetime(2026, 11, 18, 22, 5, tzinfo=UTC), {**ENTRIES, dn.KIND: [{"session": str(D)}]})
    assert dn.NoteDesk(g, inline=True).tick(done) == []  # a restart finds it in the journal
    no_plan = Live(datetime(2026, 11, 21, 22, 5, tzinfo=UTC), ENTRIES)  # no session that day
    assert dn.NoteDesk(g, inline=True).tick(no_plan) == []
