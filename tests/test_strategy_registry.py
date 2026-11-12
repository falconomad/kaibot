"""S76 (#116): strategies are plug-ins. The registry is the one list of names; the engine core holds none; every
registered strategy runs through the engine with SimBroker by one research command; a new rule plugs in without
an engine change."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pytest
import yaml

from kaibot.engine import research
from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_report
from kaibot.strategies import registry
from kaibot.strategies.base import Event, Holding, Mode, Signal, Strategy, When, due

PROF = yaml.safe_load((ROOT / "config" / "profiles.yaml").read_text())
GATE = yaml.safe_load((ROOT / "config" / "gate.yaml").read_text())


def test_every_registered_strategy_implements_the_protocol():
    for s in registry.STRATEGIES:
        assert isinstance(s, Strategy), s
        assert s.mode in Mode and s.holding in Holding and s.warmup >= 0 and s.schedule
        assert all(isinstance(w, When) for w in s.schedule)
    assert registry.names() == ("dip_reversal", "spy_late_momentum", "orb_stocks_in_play", "news_continuation")


def test_no_strategy_name_in_the_engine_core():
    core = (ROOT / "src" / "kaibot" / "engine" / "core.py").read_text()
    for name in registry.names():
        assert name not in core, name
    assert not re.search(r"\b(spymom|ATR_MULT|TARGET_R|TRAIL_ATR)\b", core)


def test_the_unused_dip_module_is_gone():
    assert not (ROOT / "src" / "kaibot" / "strategies" / "dip_reversal.py").exists()


def test_config_files_name_only_registered_strategies():
    live = set(registry.names(Mode.LIVE))
    for lv in PROF["levels"].values():
        assert set(lv["strategies"]) <= live - registry.RETIRED
    assert tuple(PROF["shadow_strategies"]) == registry.names(Mode.SHADOW)
    assert set(GATE["fingerprint"]["strategies"]) == set(registry.names())
    for files in GATE["fingerprint"]["strategies"].values():
        assert all((ROOT / f).exists() for f in files), files
    assert set(GATE["random_baseline"]) <= set(gate_report.RANDOM_MODELS)


def test_schedules():
    every5 = When(Event.BAR_CLOSE, every=5, offset=4)
    assert [m for m in range(20) if every5.due(Event.BAR_CLOSE, m, 390)] == [4, 9, 14, 19]
    once = When(Event.BAR_CLOSE, minute=359, full_session_only=True)
    assert once.due(Event.BAR_CLOSE, 359, 390) and not once.due(Event.BAR_CLOSE, 359, 210)
    assert not once.due(Event.AFTER_CLOSE, 359, 390)
    assert due((When(Event.AFTER_CLOSE),), Event.AFTER_CLOSE)


@dataclass
class BuyFirstAtTen:
    """A toy rule that is not in the engine at all: buy the first stock at 10:00 with a 1% stop and target."""

    name: str = "toy_buy_at_ten"
    mode: Mode = Mode.LIVE
    holding: Holding = Holding.INTRADAY
    schedule: tuple = (When(Event.BAR_CLOSE, minute=29),)
    warmup: int = 0
    owner_filters: bool = False
    room: bool = False

    def on_event(self, ctx):
        s = ctx.market.symbols[0]
        ref = ctx.market.last_close(s)
        if ref and not ctx.holds(s):
            yield Signal(s, ref, ref * 0.99, ref * 1.01, {"m": ctx.m}, ["toy"], None, sigma_1m=0.0, atr=0.0)

    def on_fill(self, fill, risk_ps):
        return fill - risk_ps, fill + risk_ps

    def manage(self, ctx, pos):
        return None

    def close_out(self, ctx, pos):
        return None


def test_a_new_strategy_plugs_in_and_runs_through_the_research_command(monkeypatch):
    toy = BuyFirstAtTen()
    monkeypatch.setattr(registry, "STRATEGIES", (*registry.STRATEGIES, toy))
    monkeypatch.setattr(registry, "BY_NAME", {**registry.BY_NAME, toy.name: toy})
    day = research.load_bars(research.FIXTURE)["days"][0]
    rep = research.run(toy.name, days=[day])
    assert rep["errors"] == [] and rep["ticks"] == 392
    assert rep["signals"] == 1 and rep["trades"] == 1, rep
    assert set(rep["exit_reasons"]) <= {"stop", "target", "time"}


def test_research_runs_the_dip_rule_alone_and_refuses_unknown_or_multi_day(monkeypatch):
    day = research.load_bars(research.FIXTURE)["days"][0]
    rep = research.run("dip_reversal", days=[day])
    assert rep["errors"] == [] and rep["trades"] >= 1 and rep["signals"] >= rep["trades"]
    with pytest.raises(SystemExit, match="unknown strategy"):
        research.run("nope")
    md = BuyFirstAtTen(name="toy_multi_day", holding=Holding.MULTI_DAY)
    monkeypatch.setattr(registry, "BY_NAME", {**registry.BY_NAME, md.name: md})
    with pytest.raises(SystemExit, match="S77-S78"):
        research.run(md.name)
    assert np.isfinite(rep["pnl_venue_usd"])
