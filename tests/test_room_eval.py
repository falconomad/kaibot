"""Issue #82: the offline evaluation set for the agent room. No network, no key, deterministic."""

import importlib.util
import json
import socket
from datetime import date
from pathlib import Path

import pytest

from kaibot.agents.evalset import (
    FORMAT_VERSION,
    SYNTHETIC_SET,
    EvalCase,
    after_cutoff,
    contract_violations,
    dump_cases,
    load_cases,
    replay,
    report,
)
from kaibot.agents.evalset.export import case_from_row, export_real_cases, session_query
from kaibot.agents.evalset.runner import offline_gateway
from kaibot.agents.evalset.stub import StubModel
from kaibot.agents.room import RoomOutcome

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {"clear_veto", "clear_pass", "downgrade", "missing_evidence", "stale_evidence", "pump_risk"}


def script():
    spec = importlib.util.spec_from_file_location("eval_room", ROOT / "scripts" / "eval_room.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network use in an offline evaluation")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def real_case(i: int, day: str) -> EvalCase:
    c = load_cases()[0].model_dump(mode="json")
    return EvalCase.model_validate({**c, "case_id": f"real-{i}", "synthetic": False, "expected": None,
                                    "decision_ts": f"{day}T15:00:00Z"})


# ---------- the set ----------
def test_synthetic_set_is_versioned_marked_and_covers_the_rules():
    cases = load_cases()
    assert 20 <= len(cases) <= 30
    assert all(c.synthetic and c.format_version == FORMAT_VERSION and c.outcome.source == "synthetic"
               and c.case_id.startswith("syn-") and c.signal.symbol.startswith("SYN") for c in cases)
    assert {c.category for c in cases} == CATEGORIES
    assert {c.expected.verdict for c in cases} == {"allow", "downgrade", "veto"}
    assert {c.outcome.label for c in cases} == {"win", "loss"}
    for c in cases:  # the outcome is never part of what the room is shown
        assert "outcome" not in json.dumps(c.evidence) and "r_multiple" not in json.dumps(c.candidate())


def test_unknown_version_and_duplicates_are_rejected(tmp_path):
    line = SYNTHETIC_SET.read_text().splitlines()[0]
    bad = tmp_path / "v2.jsonl"
    bad.write_text(line.replace('"format_version": 1', '"format_version": 2') + "\n")
    with pytest.raises(ValueError, match="format_version 2"):
        load_cases(bad)
    bad.write_text(line + "\n" + line + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(bad)


def test_dump_and_load_round_trip(tmp_path):
    cases = load_cases()
    dump_cases(cases, tmp_path / "x.jsonl")
    assert load_cases(tmp_path / "x.jsonl") == cases


# ---------- the stub run ----------
def test_stub_run_is_offline_deterministic_and_clean(no_network):
    cases = load_cases()
    results, not_run = replay(cases)
    rep = report(results)
    assert not_run == [] and rep["cases"] == len(cases)
    assert rep["contract_violations"] == []
    assert rep["failed_closed_rate"] == 0.0
    assert rep["expected_verdict_match"] == 1.0  # the stub applies the room's written rules
    assert 0 < rep["veto_rate"] < 1 and 0 < rep["downgrade_rate"] < 1 and 0 < rep["allow_rate"] < 1
    assert abs(rep["veto_rate"] + rep["downgrade_rate"] + rep["allow_rate"] - 1.0) < 1e-3
    assert 0 < rep["agreement_with_outcomes"] < 1
    assert 0 < rep["cost_per_decision_usd"] < 0.10 and rep["cost_source"] == "stub estimate"
    assert all(len(r.outcome.calls) == 3 for r in results)
    assert report(replay(cases)[0]) == rep  # same answer every time


def test_agreement_and_lift_arithmetic():
    cases = load_cases()[:4]
    for c, (label, r) in zip(cases, [("loss", -1.0), ("win", 2.0), ("win", 1.0), ("loss", -1.0)], strict=True):
        c.outcome.label, c.outcome.r_multiple = label, r
    outs = [RoomOutcome("done", "veto"), RoomOutcome("done", "veto"), RoomOutcome("done", "downgrade"),
            RoomOutcome("invalid", None)]
    from kaibot.agents.evalset.runner import Result

    rep = report([Result(c, o) for c, o in zip(cases, outs, strict=True)])
    assert rep["agreement_with_outcomes"] == (1 + 0 + 0.5 + 1) / 4
    assert rep["lift_r"] == 1.0 - 2.0 - 0.5 + 1.0
    assert rep["veto_rate"] == 0.5 and rep["downgrade_rate"] == 0.25 and rep["failed_closed_rate"] == 0.25


# ---------- the contract: the room may only veto or halve ----------
@pytest.mark.parametrize("misbehave", ["sizing", "double", "invent"])
def test_a_model_that_breaks_the_contract_is_contained_by_the_room(misbehave, no_network):
    results, _ = replay(load_cases(), offline_gateway(StubModel(misbehave)))
    assert [v for r in results for v in r.violations] == []
    if misbehave == "sizing":  # extra price/size fields are dropped by the schema; nothing larger than 1x comes out
        assert all(r.outcome.trade_multiplier in (0.0, 0.5, 1.0) for r in results)
        assert all("size" not in (r.outcome.judge or {}) for r in results)
    else:
        assert all(r.outcome.status == "invalid" and r.outcome.trade_multiplier == 0.0 for r in results)


class Bent(RoomOutcome):
    mult = 2.0

    @property
    def trade_multiplier(self) -> float:
        return self.mult


def test_contract_check_catches_every_kind_of_violation():
    c = load_cases()[5]  # a clear pass
    ok = replay([c])[0][0].outcome
    assert contract_violations(c, ok) == []

    def bent(mult=1.0, **changes):
        o = Bent(**{**ok.row(), **changes})
        o.mult = mult
        return contract_violations(c, o)

    assert any("multiplier 2.0" in v for v in bent(2.0))  # sized up
    assert any("fail closed" in v for v in bent(1.0, status="timeout", verdict=None))
    assert any("fail closed" in v for v in bent(0.0, status="error", verdict="allow"))
    assert any("not allow, downgrade or veto" in v for v in bent(1.0, verdict="buy_more"))
    assert any("with multiplier" in v for v in bent(1.0, verdict="downgrade"))
    assert any("traded a company_news" in v for v in bent(1.0, judge={**ok.judge, "cause": "company_news"}))
    assert any("traded a unknown" in v for v in bent(0.5, verdict="downgrade", judge={**ok.judge, "cause": "unknown"}))
    assert any("overridden" in v for v in bent(1.0, judge={**ok.judge, "verdict": "veto"}))
    assert any("another candidate" in v for v in bent(1.0, judge={**ok.judge, "candidate_id": "x"}))
    assert any("not in the pack" in v for v in bent(1.0, judge={**ok.judge, "evidence_ids": ["zz"]}))
    assert any("price/size fields ['size']" in v for v in bent(1.0, judge={**ok.judge, "size": 2}))
    assert any("unknown status" in v for v in bent(0.0, status="maybe", verdict=None))


def test_script_exits_1_on_a_violation_and_0_when_clean(monkeypatch, capsys, no_network, tmp_path):
    mod = script()
    out = tmp_path / "rep.json"
    assert mod.main(["--json", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["mode"] == "stub" and rep["contract_violations"] == [] and rep["cases"] == len(load_cases())
    text = capsys.readouterr().out
    for word in ("veto rate", "downgrade rate", "agreement with outcomes", "contract violations     0",
                 "cost per decision"):
        assert word in text

    import kaibot.agents.evalset.runner as rp

    real = rp.Room.deliberate

    def oversize(self, cid, cand, pack):
        o = real(self, cid, cand, pack)
        b = Bent(**o.row())
        b.mult = 1.5
        return b

    monkeypatch.setattr(rp.Room, "deliberate", oversize)
    assert mod.main(["--limit", "3"]) == 1
    assert "VIOLATION syn-001" in capsys.readouterr().out


# ---------- live is opt-in ----------
def test_default_run_never_builds_the_live_gateway(monkeypatch, no_network):
    mod = script()
    monkeypatch.setattr(mod, "live_gateway", lambda env: pytest.fail("live gateway built without --live"))
    assert mod.main(["--limit", "2"]) == 0


def test_live_without_keys_stops_before_any_call(monkeypatch, no_network):
    mod = script()
    for k in ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit, match="--live needs"):
        mod.main(["--live"])


def test_live_on_real_cases_demands_a_cutoff(tmp_path, monkeypatch, no_network):
    mod = script()
    monkeypatch.setattr(mod, "live_gateway", lambda env: pytest.fail("must stop before the gateway"))
    p = tmp_path / "real.jsonl"
    dump_cases([real_case(1, "2026-10-01")], p)
    with pytest.raises(SystemExit, match="--cutoff"):
        mod.main(["--live", "--set", str(p)])


def test_max_usd_stops_a_run_and_reports_what_was_not_run(no_network):
    cases = load_cases()
    results, not_run = replay(cases, offline_gateway(), max_usd=0.02)
    assert 0 < len(results) < len(cases) and len(results) + len(not_run) == len(cases)
    assert sum(r.outcome.cost_usd for r in results[:-1]) < 0.02

    gw = offline_gateway()
    gw.store.rpc = lambda fn, args: {"state": "rules_only"} if fn == "llm_budget_state" else None
    assert replay(cases, gw, max_usd=5.0) == ([], cases)  # the shared budget is not ok: nothing is run


# ---------- the training-cutoff filter ----------
def test_cutoff_keeps_only_real_cases_decided_strictly_after_it():
    real = [real_case(1, "2026-08-30"), real_case(2, "2026-08-31"), real_case(3, "2026-09-01"),
            real_case(4, "2026-10-15")]
    syn = load_cases()[:2]
    kept, dropped = after_cutoff(real + syn, date(2026, 8, 31))
    assert [c.case_id for c in kept] == ["real-3", "real-4", "syn-001", "syn-002"]
    assert [c.case_id for c in dropped] == ["real-1", "real-2"]
    kept, dropped = after_cutoff(real + syn, date(2026, 8, 31), margin_days=30)
    assert [c.case_id for c in kept] == ["real-4", "syn-001", "syn-002"] and len(dropped) == 3
    kept, _ = after_cutoff(real, date(2027, 1, 1))
    assert kept == []


def test_cutoff_uses_the_utc_decision_date():
    late = EvalCase.model_validate({**real_case(9, "2026-08-31").model_dump(mode="json"),
                                    "decision_ts": "2026-08-31T23:30:00-04:00"})  # 1 Sep 03:30 UTC
    assert after_cutoff([late], date(2026, 8, 31))[0] == [late]


class ReadOnlyStore:
    def __init__(self, sessions, trades=()):
        self.sessions, self.trades, self.queries = list(sessions), list(trades), []

    def select(self, table, params):
        self.queries.append((table, params))
        return self.sessions if table == "room_session" else self.trades


def session_row(i, ts, pnl=-40.0, strategy="dip_reversal"):
    c = load_cases()[0]
    return {"id": i, "ts": ts, "status": "done", "verdict": "veto", "evidence": c.evidence,
            "counterfactual": {"pnl_usd": pnl, "r_multiple": pnl / 40.0},
            "signal": {"id": 100 + i, "symbol": "TEST", "ts": ts, "strategy": strategy,
                       "features": {"z": -2.5, "room_latency_ms": 900, "quote_age_s": 2}}}


def test_exporter_query_and_rows_respect_the_cutoff():
    q = session_query(date(2026, 8, 31), margin_days=30)
    assert q["ts"] == "gte.2026-10-01T00:00:00+00:00"
    assert q["counterfactual"] == "not.is.null" and q["evidence"] == "not.is.null"

    rows = [session_row(1, "2026-08-31T15:00:00+00:00"),  # on the cutoff day: dropped even if the query let it by
            session_row(2, "2026-09-02T15:00:00+00:00"), session_row(3, "2026-09-03T15:00:00+00:00", pnl=55.0),
            session_row(4, "2026-09-04T15:00:00+00:00", strategy="spy_momentum")]
    store = ReadOnlyStore(rows, [{"signal_id": 103, "pnl_usd": 20.0, "risk_usd": 40.0, "exit_reason": "target",
                                  "verdict": "downgrade"}])
    cases = export_real_cases(store, date(2026, 8, 31))
    assert [c.case_id for c in cases] == ["real-2", "real-3"]
    assert [c.outcome.label for c in cases] == ["loss", "win"] and not any(c.synthetic for c in cases)
    assert "realised trade pnl_usd=20.0" in cases[1].outcome.source
    assert cases[0].signal.features == {"z": -2.5}  # fields the room itself added are not fed back to it
    assert [t for t, _ in store.queries] == ["room_session", "trade"]
    assert replay(cases)[0][0].violations == []  # an exported case replays like a synthetic one


def test_rows_without_a_pack_or_an_outcome_are_skipped():
    r = session_row(1, "2026-09-02T15:00:00+00:00")
    assert case_from_row({**r, "counterfactual": None}) is None
    assert case_from_row({**r, "evidence": {"items": []}}) is None


# ---------- the engine is untouched ----------
def test_engine_does_not_import_the_evalset_and_the_room_is_still_off_by_default():
    for f in (ROOT / "src" / "kaibot" / "engine").glob("*.py"):
        assert "evalset" not in f.read_text(), f.name
    from fakes import FakeStore

    from kaibot.agents.gateway import from_env

    assert from_env(FakeStore(), {}).enabled is False
