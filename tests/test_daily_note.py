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


def test_the_desk_writes_once_from_1610_et():
    _, llm, g = gw({"paragraph": GOOD})
    desk = dn.NoteDesk(g, inline=True)
    before = Live(datetime(2026, 11, 18, 21, 5, tzinfo=UTC), ENTRIES)  # 16:05 ET
    assert desk.tick(before) == [] and llm.calls == []
    after = Live(datetime(2026, 11, 18, 21, 10, tzinfo=UTC), ENTRIES)  # 16:10 ET: inside the session (ends 16:30)
    recs = desk.tick(after)
    assert len(recs) == 1 and recs[0]["source"] == "model" and recs[0]["session"] == str(D)
    assert desk.tick(after) == [] and len(llm.calls) == 1  # once a day
    later = Live(datetime(2026, 11, 18, 21, 29, tzinfo=UTC), ENTRIES)  # the session's last tick
    assert desk.tick(later) == [] and desk.tick(later, final=True) == [] and len(llm.calls) == 1  # never twice
    done = Live(datetime(2026, 11, 18, 21, 10, tzinfo=UTC), {**ENTRIES, dn.KIND: [{"session": str(D)}]})
    assert dn.NoteDesk(g, inline=True).tick(done) == []  # a restart finds it in the journal
    assert dn.NoteDesk(g, inline=True).tick(done, final=True) == []
    no_plan = Live(datetime(2026, 11, 21, 21, 10, tzinfo=UTC), ENTRIES)  # no session that day
    assert dn.NoteDesk(g, inline=True).tick(no_plan) == []
    assert dn.NoteDesk(g, inline=True).tick(no_plan, final=True) == []


def test_the_note_time_is_inside_the_live_session():
    from datetime import time

    from kaibot.engine import live_daily as ld
    from kaibot.engine.runner import AFTER_CLOSE

    session_end = (datetime.combine(D, time(16, 0)) + AFTER_CLOSE).time()  # the engine's last tick: 16:30 ET
    assert ld.SEND_UNTIL < dn.NOTE_FROM < session_end
    assert dn.CLOSE <= dn.NOTE_FROM


CARRY_DAY = date(2026, 11, 19)
CARRY = {  # #366: a carry-over day: the send is journalled as `md_send` of THAT session, with its `carry` note
    "md_send": ENTRIES["md_send"] + [
        {"run_id": 1, "strategy": R, "session": str(CARRY_DAY), "decided": str(D),
         "targets": [["AAA", 20, "carry", "rank 3"]],
         "carry": {"of": str(D), "carried": [["AAA", 20]], "skipped": {"CCC": "market filter off"},
                   "risk_off": False}}],
    "fill": ENTRIES["fill"] + [{"strategy": R, "session": str(CARRY_DAY), "symbol": "AAA", "side": "buy",
                                "qty": 8.0, "px": 102.0, "purpose": "carry"}],
}


def test_a_carry_over_day_gets_its_note():
    _, llm, g = gw({"paragraph": "x"})
    g.client = None  # no key: the template, from the carry day's own records
    recs = dn.NoteDesk(g, inline=True).tick(Live(datetime(2026, 11, 19, 21, 10, tzinfo=UTC), CARRY))
    assert len(recs) == 1 and recs[0]["session"] == str(CARRY_DAY)
    r = recs[0]["facts"]["rules"][R]
    assert r["plan"] == [{"symbol": "AAA", "target_shares": 20, "purpose": "carry", "reason": "rank 3"}]
    assert r["bought"] == [{"symbol": "AAA", "shares": 8.0, "price": 102.0}]
    assert r["not_carried_over"] == [{"symbol": "CCC", "why": "market filter off"}]
    assert "OLD" not in json.dumps(recs[0]["facts"]) and "BBB" not in json.dumps(r)  # other sessions' records


def test_the_engine_stop_writes_a_missing_note_once_after_the_close():
    _, llm, g = gw({"paragraph": GOOD})
    desk = dn.NoteDesk(g)  # a worker thread on ticks; the stop writes in its own thread
    early = Live(datetime(2026, 11, 18, 20, 59, tzinfo=UTC), ENTRIES)  # 15:59 ET: the day is not over
    assert desk.tick(early, final=True) == [] and llm.calls == []
    stop = Live(datetime(2026, 11, 18, 21, 5, tzinfo=UTC), ENTRIES)  # 16:05 ET: stopped before the 16:10 tick
    assert desk.tick(stop) == []
    recs = desk.tick(stop, final=True)
    assert len(recs) == 1 and recs[0]["session"] == str(D) and len(llm.calls) == 1
    assert desk.tick(stop, final=True) == [] and desk.tick(stop) == [] and len(llm.calls) == 1


def test_the_engine_stop_waits_for_a_note_being_written():
    import threading

    _, llm, g = gw({"paragraph": GOOD})
    desk = dn.NoteDesk(g)
    gate = threading.Event()
    real = desk._write
    desk._write = lambda day, f: (gate.wait(5), real(day, f))
    at = Live(datetime(2026, 11, 18, 21, 10, tzinfo=UTC), ENTRIES)
    assert desk.tick(at) == []  # started in a worker thread
    threading.Timer(0.05, gate.set).start()
    recs = desk.tick(at, final=True)
    assert len(recs) == 1 and len(llm.calls) == 1


def test_the_desk_never_raises():
    class Broken(Live):
        def _entries(self, kind):
            raise RuntimeError("journal unreadable")

    class BadClock:
        def now(self):
            raise RuntimeError("no clock")

    _, llm, g = gw({"paragraph": GOOD})
    for live in (Broken(datetime(2026, 11, 18, 21, 10, tzinfo=UTC), ENTRIES), BadClock(), None):
        for final in (False, True):
            assert dn.NoteDesk(g, inline=True).tick(live, final=final) == []
    boom = Gateway(FakeStore(), FakeLLM({}), CFG)  # enabled, and its call raises: the template
    boom.call = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gateway down"))
    recs = dn.NoteDesk(boom, inline=True).tick(Live(datetime(2026, 11, 18, 21, 10, tzinfo=UTC), ENTRIES),
                                               final=True)
    assert len(recs) == 1 and recs[0]["source"] == "template" and "gateway down" in recs[0]["why"]


def test_the_engine_stop_waits_a_bounded_time(monkeypatch):
    import threading
    import time as _time

    monkeypatch.setattr(dn, "FINAL_WAIT_MAX_S", 0.2)
    _, llm, g = gw({"paragraph": GOOD})
    assert dn.final_wait_s(g) == 0.2 and dn.final_wait_s(None) == 0.2
    hang = threading.Event()
    for started in (False, True):  # the stop starts the note itself, or finds one in flight
        desk = dn.NoteDesk(g)
        desk._write = lambda day, f: hang.wait(30)  # a database that keeps retrying
        at = Live(datetime(2026, 11, 18, 21, 10, tzinfo=UTC), ENTRIES)
        if started:
            assert desk.tick(at) == []
        t0 = _time.monotonic()
        assert desk.tick(at, final=True) == []
        assert _time.monotonic() - t0 < 2.0
    hang.set()


def test_a_failed_sync_starts_no_note_and_keeps_its_error():
    from kaibot.engine import live_daily as ld

    live = ld.LiveDaily.__new__(ld.LiveDaily)
    steps: list = []
    live._shadow_collect = lambda: None
    live._note_step = lambda final=False: steps.append(final)
    live._sync = lambda force: (_ for _ in ()).throw(RuntimeError("broker down"))
    with pytest.raises(RuntimeError, match="broker down"):
        live.sync()
    assert steps == []  # its facts could miss this tick's fills
    live._sync = lambda force: None  # the early returns included
    assert live.sync() is None and steps == [False]


@pytest.mark.parametrize("text", [
    "Today kaibo bought 7 AAA.",                          # a small count the facts do not have
    "Equity at the last close was $50,000.",              # a round number ("50000" is not 5)
    "It sold 80 BBB and is up 10%.",                      # trailing zeros of whole numbers
    "Today kaibo bought five AAA.",                       # a number in words
    "It bought twenty-one AAA for a thousand dollars.",   # compound and scale words
    "AAA ranked fifth.",                                  # an ordinal
])
def test_invented_numbers_are_caught_in_every_form(text):
    """QA-Ops on #318: each of these passed the facts-only check before."""
    assert dn.unknown_numbers(text, F()) != []
    _, _, g = gw({"paragraph": GOOD + " " + text})
    assert dn.write(D, F(), g)["source"] == "template"


def test_numbers_the_facts_hold_pass_in_any_form():
    ok = "It bought twelve AAA, sold eight BBB at $55.50, holds two stocks and AAA ranked third; $100,512 in all."
    assert dn.unknown_numbers(ok, F()) == []
