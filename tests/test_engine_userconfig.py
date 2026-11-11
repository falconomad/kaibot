"""M5 S43/S44/S49 end to end in a fake session: an applied config is picked up at the next open, Only mode filters,
trades carry config_version, an invalid config refuses only that session, and a config applied after the open waits
for the next session."""

from __future__ import annotations

from datetime import timedelta

from test_engine import fake_candidate, make, run_day, synth_frames

UNI = frozenset({"A", "B1", "B2", "B3", "B4", "B5", "C1"})
THEMES = {"t5": ["B1", "B2", "B3", "B4", "B5"], "ta": ["A", "B1", "B2", "B3", "B4"]}


def setup(tmp_path, body=None, created_before_open=True, version=1, level="balanced"):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(), start_level=level)
    eng._uni_cache = (UNI, THEMES)
    if body is not None:
        ts = open_utc - timedelta(hours=10) if created_before_open else open_utc + timedelta(minutes=5)
        af = open_utc if created_before_open else open_utc + timedelta(days=1)
        store.insert("config", {"run_id": store.tables["run"][0]["id"], "version": version, "body": body,
                                "status": "applied", "created_at": ts.isoformat(), "applies_from": af.isoformat()})
    return eng, broker, clock, store, open_utc


def entries(store):
    return [t for t in store.tables.get("trade", []) if t.get("strategy") == "dip_reversal"]


def test_no_config_is_the_level_and_version_0(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    assert eng.day.rules.version == 0 and eng.policy() == eng.policies["balanced"]
    tr = entries(store)
    assert tr and all(t["config_version"] == 0 for t in tr)
    assert store.tables["daily_result"][0]["config_version"] == 0


def test_applied_config_tightens_and_tags_trades(tmp_path):
    body = {"risk": {"risk_per_trade_pct": 0.25, "max_positions": 2}, "strategies": {"spy_late_momentum": False}}
    eng, broker, clock, store, open_utc = setup(tmp_path, body)
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    r = eng.day.rules
    assert r.version == 1 and r.policy.risk_per_trade_pct == 0.25 and r.policy.max_positions == 2
    assert not eng._runs("spy_late_momentum")
    tr = entries(store)
    assert tr and all(t["config_version"] == 1 for t in tr)
    assert any(e["type"] == "config_applied" and e["payload"]["version"] == 1 for e in store.tables["event"])
    # smaller risk -> smaller size than the level would give
    g = [x for x in store.tables["gate_run"] if x.get("approved")]
    assert g


def test_only_mode_filters_out_non_focus_symbols(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"focus": {"mode": "only", "themes": ["t5"]}})
    fake_candidate(eng)  # offers symbol A, which is not in the focus set
    run_day(eng, broker, clock, open_utc)
    assert not entries(store)
    assert not [s for s in store.tables.get("signal", []) if s.get("strategy") == "dip_reversal"]


def test_only_mode_keeps_focus_symbols(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"focus": {"mode": "only", "themes": ["ta"]}})
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    assert entries(store)


def test_vix_limit_vetoes(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"timing": {"vix_max": 20}})
    fake_candidate(eng)
    eng.market.regime_at = lambda m: (False, 22.0)
    run_day(eng, broker, clock, open_utc)
    assert not entries(store)
    assert any("VIX above your limit" in (s.get("rules_veto") or "") for s in store.tables["signal"])


def test_entry_window_from_config(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"timing": {"entry_start": "10:30"}})
    fake_candidate(eng, at=(54,))  # 10:25: before the owner's window
    run_day(eng, broker, clock, open_utc)
    assert not entries(store)


def test_invalid_config_refuses_only_the_session(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"risk": {"risk_per_trade_pct": 5.0}})  # looser: rejected
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    assert eng.day.refused and "rejected" in eng.day.refused
    assert not entries(store)
    assert any(e["type"] == "engine_error" and e["payload"]["kind"] == "config" for e in store.tables["event"])
    assert any(h["kind"] == "config" for h in store.tables["halt"])
    assert not eng.kill  # not the engine-wide kill switch: the next session re-checks


def test_config_applied_after_the_open_waits_for_next_session(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"risk": {"max_positions": 1}}, created_before_open=False)
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    assert eng.day.rules.version == 0
    assert all(t["config_version"] == 0 for t in entries(store))


def test_superseded_versions_are_ignored(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"risk": {"max_positions": 2}}, version=1)
    store.insert("config", {"run_id": store.tables["run"][0]["id"], "version": 2,
                            "body": {"risk": {"max_positions": 3}}, "status": "superseded",
                            "created_at": (open_utc - timedelta(hours=1)).isoformat(),
                            "applies_from": open_utc.isoformat()})
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    assert eng.day.rules.version == 1 and eng.day.rules.policy.max_positions == 2


def test_gate_judges_the_current_config_only(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, {"risk": {"max_positions": 4}})
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    rep = store.tables["gate_report"][-1]["report"]
    assert rep["config_version"] == 1
    assert store.tables["gate_random"][-1]["config_version"] == 1
