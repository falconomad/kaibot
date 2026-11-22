"""#560 (QA on #571): the run scripts take their default control runs from config/gate_v3.yaml
(`gate_v3.control_runs_for`: 2,000 for a rule whose family lists several rules, 500 for a one-rule family), and an
explicit `--control-runs` is kept as given. Checked through each script's `--has` path, which passes the resolved
number to `find_report`; a hardcoded default (500) makes these fail."""

from __future__ import annotations

import sys

import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3

sys.path.insert(0, str(ROOT / "scripts"))
import momentum_core  # noqa: E402
import oneshots  # noqa: E402
import spy_engine  # noqa: E402
import spy_noise_band  # noqa: E402

GATE = gate_v3.GateV3.load(ROOT)
COMMON = ["--start", "2016-01-01", "--end", "2024-12-31"]


def _resolved(monkeypatch, module, argv: list[str], **patches) -> int:
    seen: dict = {}

    def fake_find(*args, **kw):
        seen["runs"] = kw["control_runs"]
        return True

    monkeypatch.setattr(module, "find_report", fake_find)
    for name, fn in patches.items():
        monkeypatch.setattr(module, name, fn)
    monkeypatch.setattr(sys, "argv", [module.__file__, *argv])
    with pytest.raises(SystemExit) as e:
        module.main()
    assert e.value.code == 0
    return seen["runs"]


@pytest.mark.parametrize("rule", ["trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"])
def test_momentum_core_defaults_to_the_gate_config_and_keeps_an_explicit_value(monkeypatch, tmp_path, rule):
    pat = {"universe_mode": lambda a: ("config/universe_history.yaml", "today", "store"),
           "earnings_input": lambda a, data, mode: "off"}
    argv = ["--has", rule, "--data", str(tmp_path), *COMMON]
    want = gate_v3.control_runs_for(GATE, rule)
    assert want == 2000  # its family has several rules
    assert _resolved(monkeypatch, momentum_core, argv, **pat) == want
    assert _resolved(monkeypatch, momentum_core, [*argv, "--control-runs", "700"], **pat) == 700


@pytest.mark.parametrize("rule", ["pullback_uptrend", "ath_breakout", "news_gap_week", "post_earnings_week"])
def test_oneshots_defaults_to_the_gate_config_and_keeps_an_explicit_value(monkeypatch, tmp_path, rule):
    argv = ["--has", rule, "--data", str(tmp_path), *COMMON]
    want = gate_v3.control_runs_for(GATE, rule)
    assert _resolved(monkeypatch, oneshots, argv) == want
    assert _resolved(monkeypatch, oneshots, [*argv, "--control-runs", "700"]) == 700


def test_oneshots_multi_rule_families_get_2000():
    assert {r: gate_v3.control_runs_for(GATE, r) for r in ("pullback_uptrend", "ath_breakout", "news_gap_week")} \
        == dict.fromkeys(("pullback_uptrend", "ath_breakout", "news_gap_week"), 2000)


@pytest.mark.parametrize("version", ["spy_noise_band", "spy_noise_band_carry"])
def test_spy_noise_band_defaults_to_the_gate_config_and_keeps_an_explicit_value(monkeypatch, tmp_path, version):
    argv = ["--has", version, "--data", str(tmp_path), *COMMON]
    assert _resolved(monkeypatch, spy_noise_band, argv) == gate_v3.control_runs_for(GATE, version) == 2000
    assert _resolved(monkeypatch, spy_noise_band, [*argv, "--control-runs", "700"]) == 700


def test_spy_engine_defaults_to_the_gate_config_and_keeps_an_explicit_value(monkeypatch, tmp_path):
    seen: dict = {}
    monkeypatch.setattr(spy_engine, "find_report", lambda *a, **kw: seen.update(runs=kw["control_runs"]) or True)
    argv = ["--has", "--data", str(tmp_path), *COMMON]
    assert spy_engine.main(argv) == 0
    assert seen["runs"] == gate_v3.control_runs_for(GATE, spy_engine.RULE) == 2000
    assert spy_engine.main([*argv, "--control-runs", "700"]) == 0 and seen["runs"] == 700
