"""#133 (decision 15 section 11, stakeholder 13 Nov 2026): trend_momentum FAILED Gate B and runs on paper as a
labelled EXPERIMENT, never real money, at exposure caps of 30% / 60% / 100% (Careful / Balanced / Hunter), tighten-
only inside BOOK_HARD_CEILINGS; mom_12_1_band stays off; KAIBOT_MULTIDAY and allow_overnight stay off by default."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from kaibot.risk.gate import BOOK_HARD_CEILINGS, validate_book
from kaibot.risk.profiles import (
    DEFAULT_PATH,
    enabled_multi_day,
    experiments,
    load_book_limits,
    load_multi_day_rules,
    load_strategies,
    rule_book_limits,
    rule_exposure_pct,
)
from kaibot.strategies import registry

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MD = "trend_momentum"
CAPS = {"conservative": 30.0, "balanced": 60.0, "aggressive": 100.0}
RETIRE = {"conservative": 13.0, "balanced": 21.0, "aggressive": 27.0}
# the ten-year point-in-time test (core_pit, 13 Nov 2026): trend_momentum's max drawdown at each cap
TEST_DD = {"conservative": 8.57, "balanced": 14.18, "aggressive": 18.27}


def _cfg():
    return yaml.safe_load(DEFAULT_PATH.read_text())


def _write(tmp_path, cfg) -> Path:
    p = tmp_path / "profiles.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


# ------------------------------------------------------------------ defaults the stakeholder flips


def test_the_flag_stays_off_by_default_and_overnight_is_allowed(monkeypatch):
    """Product, 13 Nov 2026 (decision 15 section 11): allow_overnight is true in the repository; it acts only on
    multi-day books, which exist only with KAIBOT_MULTIDAY=on, the stakeholder's single switch (default off)."""
    monkeypatch.delenv(registry.MULTIDAY_FLAG, raising=False)
    assert not registry.multi_day_enabled()
    assert _cfg()["all_levels"]["allow_overnight"] is True
    # with the flag off the levels run exactly their own (intraday) lists
    assert load_strategies() == {lv: list(body["strategies"]) for lv, body in _cfg()["levels"].items()}


def test_trend_momentum_is_the_only_multi_day_rule_on_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv(registry.MULTIDAY_FLAG, "on")
    assert enabled_multi_day() == [MD]
    st = load_strategies()
    for lv, names in st.items():
        assert names == [*_cfg()["levels"][lv]["strategies"], MD]
        assert registry.multi_day_names(names) == [MD]
    assert not load_multi_day_rules()["mom_12_1_band"].enabled


# ------------------------------------------------------------------ the caps


def test_level_caps_are_30_60_100_and_inside_the_hard_ceilings(monkeypatch):
    rules = load_multi_day_rules()
    assert rules[MD].max_exposure_pct == CAPS
    for lv, cap in CAPS.items():
        assert rule_exposure_pct(MD, lv) == cap
        research = rule_book_limits(MD, lv, research=True)
        assert research.max_gross_pct == research.max_overnight_pct == cap
        validate_book(research, BOOK_HARD_CEILINGS)
        validate_book(research, load_book_limits(research=True)[lv])  # tighten-only on top of the level
        live = rule_book_limits(MD, lv)  # allow_overnight true: the cap is also the overnight cap
        assert live.max_gross_pct == live.max_overnight_pct == cap
        validate_book(live, load_book_limits()[lv])
    assert rule_exposure_pct("mom_12_1_band", "balanced") == 0.0  # no cap configured: may hold nothing


def test_with_overnight_off_nothing_is_held_over_the_close(tmp_path):
    cfg = _cfg()
    cfg["all_levels"]["allow_overnight"] = False
    p = _write(tmp_path, cfg)
    for lv, cap in CAPS.items():
        lim = rule_book_limits(MD, lv, p)
        assert lim.max_gross_pct == cap and lim.max_overnight_pct == 0


@pytest.mark.parametrize("bad,match", [(101, "loosens the hard ceiling"), (0, "must be a number > 0"),
                                       (-5, "must be a number > 0"), ("60", "must be a number > 0"),
                                       (True, "must be a number > 0"), (float("nan"), "must be a number > 0")])
def test_a_cap_that_loosens_or_is_malformed_does_not_load(tmp_path, bad, match):
    cfg = _cfg()
    cfg["multi_day"]["rules"][MD]["max_exposure_pct"]["aggressive"] = bad
    with pytest.raises(ValueError, match=match):
        load_multi_day_rules(_write(tmp_path, cfg))


def test_a_rule_that_is_on_needs_every_level(tmp_path):
    cfg = _cfg()
    del cfg["multi_day"]["rules"][MD]["max_exposure_pct"]["conservative"]
    with pytest.raises(ValueError, match="every level"):
        load_multi_day_rules(_write(tmp_path, cfg))
    cfg = _cfg()
    cfg["multi_day"]["rules"][MD]["max_exposure_pct"]["careful"] = 30
    with pytest.raises(ValueError, match="unknown level"):
        load_multi_day_rules(_write(tmp_path, cfg))


@pytest.mark.parametrize("name", ["mom_12_1_band", "mom_12_1_band_nofilter"])
def test_a_failed_or_unjudged_rule_that_is_not_an_experiment_cannot_be_switched_on(tmp_path, name):
    cfg = _cfg()
    cfg["multi_day"]["rules"][name] = {"enabled": True, "max_exposure_pct": dict(CAPS),
                                       "retire_drawdown_pct": dict(RETIRE)}
    with pytest.raises(ValueError, match="not passed Gate B"):
        load_multi_day_rules(_write(tmp_path, cfg))


def test_an_unknown_or_intraday_rule_is_refused(tmp_path):
    for name in ("dip_reversal", "no_such_rule"):
        cfg = _cfg()
        cfg["multi_day"]["rules"][name] = {"enabled": False}
        with pytest.raises(ValueError, match="not a multi-day rule"):
            load_multi_day_rules(_write(tmp_path, cfg))


def test_retirement_trigger_is_one_and_a_half_times_the_test_drawdown():
    rules = load_multi_day_rules()
    assert rules[MD].retire_drawdown_pct == RETIRE
    for lv, dd in TEST_DD.items():
        assert abs(RETIRE[lv] - 1.5 * dd) < 0.5  # rounded to whole points


# ------------------------------------------------------------------ the label


def test_registry_records_the_failed_gate_b_verdicts_and_no_real_money():
    tm, mb = registry.evidence(MD), registry.evidence("mom_12_1_band")
    assert (tm.status, tm.experiment, tm.real_money) == ("failed_gate_b", True, False)
    assert (mb.status, mb.experiment) == ("failed_gate_b", False)
    assert registry.evidence(f"{MD}~reg60") == tm  # a neighbour has its rule's verdict
    assert registry.evidence("mom_12_1_band_nofilter") is None and registry.evidence("dip_reversal") is None
    assert registry.may_run_on_paper(MD) and not registry.may_run_on_paper("mom_12_1_band")
    assert not any(e.real_money for e in registry.EVIDENCE.values())


def test_experiment_label_rows():
    assert experiments(["dip_reversal"], "balanced") == []
    rows = experiments(["dip_reversal", MD], "conservative")
    assert rows == [{"strategy": MD, "evidence": "failed_gate_b", "experiment": True,
                     "label": "Experiment: failed the ten-year test", "real_money": False, "decided": "2026-11-13",
                     "decision": registry.EVIDENCE[MD].decision, "level": "conservative", "max_exposure_pct": 30.0,
                     "retire_drawdown_pct": 13.0}]
    assert experiments([MD], "aggressive")[0]["max_exposure_pct"] == 100.0


def test_engine_puts_the_label_in_the_journal_and_the_heartbeat(tmp_path, monkeypatch):
    """The session's `session_start` event and the status dict the heartbeat writes (`detail.experiments`) carry
    the label when trend_momentum runs; an intraday-only session has neither key."""
    from test_failsafe_drills import Account

    monkeypatch.setenv(registry.MULTIDAY_FLAG, "on")
    a = Account(tmp_path)
    a.eng.strategies = load_strategies()
    a.eng.ex_dividends = lambda day, held: ()  # the ex-date source the live engine wires (S78)
    a.ticks(a.open1, 0, 2)
    starts = [e for e in a.store.tables["event"] if e["type"] == "session_start"]
    assert starts and starts[-1]["payload"]["experiments"][0]["evidence"] == "failed_gate_b"
    assert starts[-1]["payload"]["experiments"][0]["max_exposure_pct"] == 60.0  # the run is Balanced
    st = a.eng._status(1)
    assert st["experiments"][0]["label"] == "Experiment: failed the ten-year test"
    assert st["experiments"][0]["experiment"] is True


def test_intraday_only_engine_has_no_experiment_key(tmp_path, monkeypatch):
    from test_failsafe_drills import Account

    monkeypatch.delenv(registry.MULTIDAY_FLAG, raising=False)
    a = Account(tmp_path)
    a.ticks(a.open1, 0, 2)
    starts = [e for e in a.store.tables["event"] if e["type"] == "session_start"]
    assert starts and "experiments" not in starts[-1]["payload"]
    assert "experiments" not in a.eng._status(1)


def test_a_bare_on_key_is_refused(tmp_path):
    """YAML 1.1 reads `on:` as the boolean True: such a rule would silently stay off, so it does not load."""
    p = tmp_path / "profiles.yaml"
    p.write_text(DEFAULT_PATH.read_text().replace("      enabled: true", "      on: true", 1))
    with pytest.raises(ValueError, match="use `enabled"):
        load_multi_day_rules(p)


def test_check_reports_the_multi_day_switches(monkeypatch):
    from kaibot.engine.__main__ import multi_day_state

    class Remote:
        def active_run(self):
            return {"id": 1, "status": "running", "level": "conservative"}

    monkeypatch.delenv(registry.MULTIDAY_FLAG, raising=False)
    off = multi_day_state(Remote())
    assert off[registry.MULTIDAY_FLAG] == "off" and off["rules_loaded"] == [] and off["experiments"] == []
    assert off["allow_overnight"] is True
    monkeypatch.setenv(registry.MULTIDAY_FLAG, "on")
    st = multi_day_state(Remote())
    assert st["rules_loaded"] == [MD] and st["level"] == "conservative"
    assert st["overnight_cap_pct"] == {MD: 30.0}  # allow_overnight true: Careful's 30% cap
    assert st["experiments"][0]["max_exposure_pct"] == 30.0 and st["experiments"][0]["evidence"] == "failed_gate_b"


def test_allow_overnight_reaches_only_multi_day_books():
    """Flags off => no multi-day anything: allow_overnight is read only by the multi-day book limits
    (risk/profiles.load_book_limits) and the --check line; nothing on the intraday path reads it, so with
    KAIBOT_MULTIDAY off (no multi-day rule loaded, no daily path) the engine is the same with it true or false."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "kaibot"
    readers = sorted(str(f.relative_to(src)) for f in src.rglob("*.py")
                     if '"allow_overnight"' in f.read_text())
    assert readers == ["engine/__main__.py", "risk/profiles.py"]


def test_with_the_flag_off_no_multi_day_rule_or_daily_path_loads(monkeypatch):
    from kaibot.engine import __main__ as entry

    monkeypatch.delenv(registry.MULTIDAY_FLAG, raising=False)
    assert entry.daily_path() is None
    assert all(not registry.multi_day_names(v) for v in load_strategies().values())
