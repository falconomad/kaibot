"""Stories S34 and S36 inside the engine: the room vetoes or halves what the rules approved, never more; every
session is stored with the rules-only arm and a day-end counterfactual."""

from __future__ import annotations

from concurrent.futures import Future

from fakes import FakeLLM
from test_engine import fake_candidate, make, run_day, synth_frames
from test_room import PACK, answers

from kaibot.agents.gateway import Gateway, LLMConfig
from kaibot.agents.room import Room
from kaibot.engine.roomflow import RoomFlow

CFG = LLMConfig.load()


class SyncExecutor:
    """Runs submitted work immediately: room sessions are 'done' by the next tick, deterministically."""

    def submit(self, fn, *a):
        f = Future()
        try:
            f.set_result(fn(*a))
        except Exception as e:  # noqa: BLE001
            f.set_exception(e)
        return f


class NeverExecutor:
    def submit(self, fn, *a):
        return Future()  # never completes: the room "hangs"


def with_room(tmp_path, llm, executor=None, level="balanced"):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(), start_level=level)
    room = Room(Gateway(store, llm, CFG))
    eng.roomflow = RoomFlow(eng, room, pack_fn=lambda e, s, etf, t, f: PACK)
    eng.roomflow.ex = executor or SyncExecutor()
    return eng, broker, clock, store, open_utc


def cid_answers(verdict, cause="sector"):
    """The candidate id is built from run, day, symbol and minute; accept whatever the room is asked."""
    base = answers(verdict, cause)
    def fix(name):
        def f(kw):
            text = kw["messages"][0]["content"]
            cid = text.split('"id": "', 1)[1].split('"', 1)[0]
            return {**base[name], "candidate_id": cid}
        return f
    return {k: fix(k) for k in base}


def test_allow_trades_full_size_after_the_room(tmp_path):
    eng, broker, clock, store, open_utc = with_room(tmp_path, FakeLLM(cid_answers("allow")))
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc)
    (rs,) = store.tables["room_session"]
    assert rs["status"] == "done" and rs["verdict"] == "allow" and rs["rules_only"] == "allow"
    (t,) = store.tables["trade"]
    sig = next(s for s in store.tables["signal"] if s["id"] == rs["signal_id"])
    assert sig["features"]["room_submitted_m"] == 54 and "room: allow" in sig["reasons"]
    assert t["qty"] == rs["counterfactual"]["qty"]  # allowed: actual size = rules-only size
    assert rs["counterfactual"]["pnl_usd"] is not None


def test_veto_means_no_trade_but_the_counterfactual_is_recorded(tmp_path):
    eng, broker, clock, store, open_utc = with_room(tmp_path, FakeLLM(cid_answers("veto", "company_news")))
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc)
    assert "trade" not in store.tables
    (rs,) = store.tables["room_session"]
    assert rs["verdict"] == "veto" and rs["counterfactual"]["qty"] > 0
    gate = next(g for g in store.tables["gate_run"] if g["signal_id"] == rs["signal_id"])
    assert not gate["approved"]


def test_downgrade_halves_the_size(tmp_path):
    eng, broker, clock, store, open_utc = with_room(tmp_path, FakeLLM(cid_answers("downgrade")))
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc)
    (t,) = store.tables["trade"]
    (rs,) = store.tables["room_session"]
    assert t["qty"] == rs["counterfactual"]["qty"] // 2


def test_a_hung_room_is_dropped_after_two_cycles(tmp_path):
    eng, broker, clock, store, open_utc = with_room(tmp_path, FakeLLM(cid_answers("allow")), NeverExecutor())
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc, until=80)
    assert "trade" not in store.tables
    (rs,) = store.tables["room_session"]
    assert rs["status"] == "timeout" and rs["verdict"] is None


def test_budget_reached_means_rules_only(tmp_path):
    eng, broker, clock, store, open_utc = with_room(tmp_path, FakeLLM(cid_answers("veto", "company_news")))
    store.llm["day"] = CFG.daily_soft_usd  # the room would veto, but it never runs
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc)
    assert len(store.tables["trade"]) == 1
    (rs,) = store.tables["room_session"]
    assert rs["status"] == "skipped" and "budget" in rs["error"]


def test_monthly_cap_stops_new_entries(tmp_path):
    eng, broker, clock, store, open_utc = with_room(tmp_path, FakeLLM(cid_answers("allow")))
    store.llm["month"] = CFG.monthly_hard_usd
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc)
    assert "trade" not in store.tables and eng.kill == "llm_budget"


def test_room_crash_is_a_no_trade_not_an_engine_crash(tmp_path):
    eng, broker, clock, store, open_utc = with_room(tmp_path, FakeLLM(cid_answers("allow")))
    eng.roomflow.pack_fn = lambda *a: (_ for _ in ()).throw(RuntimeError("news db down"))
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc)
    assert "trade" not in store.tables
    assert store.tables["room_session"][0]["status"] == "error"


def test_news_veto_never_reaches_the_room(tmp_path):
    llm = FakeLLM(cid_answers("allow"))
    eng, broker, clock, store, open_utc = with_room(tmp_path, llm)
    fake_candidate(eng, at=(54,))
    eng.market.data.news_count = lambda *a: 2
    run_day(eng, broker, clock, open_utc)
    assert llm.calls == [] and "room_session" not in store.tables


def test_extra_rules_veto_blocks_before_the_room(tmp_path):
    llm = FakeLLM(cid_answers("allow"))
    eng, broker, clock, store, open_utc = with_room(tmp_path, llm)
    eng.rules_extra = lambda e, s, m: "pump risk"
    fake_candidate(eng, at=(54,))
    run_day(eng, broker, clock, open_utc)
    assert llm.calls == [] and "trade" not in store.tables
    assert any(s["rules_veto"] == "pump risk" for s in store.tables["signal"])
