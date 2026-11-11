"""M5 S51/S53: config/presets.yaml is the one spec for presets and Boldness; the dashboard's JSON copy matches it;
every step and preset, clamped to each level, is a legal tightening (the server would reject anything looser)."""

from __future__ import annotations

import importlib.util
import json

import yaml

from kaibot.engine.config import ROOT
from kaibot.engine.userconfig import MAX_EXCLUDED, RISK_FIELDS, ConfigError, is_tightening, tighten
from kaibot.risk.profiles import load_profiles, load_strategies

P = yaml.safe_load((ROOT / "config" / "presets.yaml").read_text())
PROFILES, STRATS = load_profiles(), load_strategies()
U = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
UNI = frozenset(s for v in U["sectors"].values() for s in v)
THEMES = {k: v["symbols"] for k, v in U["themes"].items()}


def test_web_copy_is_in_sync():
    spec = importlib.util.spec_from_file_location("g", ROOT / "scripts" / "gen_presets_json.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    assert (ROOT / "apps" / "web" / "src" / "data" / "presets.json").read_text() == g.build(), \
        "run python scripts/gen_presets_json.py"


def test_max_step_equals_hunter_and_steps_are_monotone():
    steps = P["boldness"]
    assert len(steps) == 5
    for k in RISK_FIELDS:
        vals = [s[k] for s in steps]
        assert vals == sorted(vals), k
        attr = RISK_FIELDS[k][1]
        assert steps[-1][k] == getattr(PROFILES["aggressive"], attr)
        assert steps[0][k] == getattr(PROFILES["conservative"], attr)


def body_for(step, level, hours="all", caution=(), focus=None):
    lp = PROFILES[level]
    risk = {k: min(v, getattr(lp, RISK_FIELDS[k][1])) for k, v in P["boldness"][step].items() if k != "name"}
    h = next(x for x in P["hours"] if x["key"] == hours)
    hm = lambda m: f"{(570 + m) // 60:02d}:{(570 + m) % 60:02d}"  # noqa: E731
    timing = {"entry_start": hm(max(5, h["from"])), "entry_end": hm(min(375, h["to"]))}
    for c in caution:
        timing.update({k: v for k, v in P["caution"][c].items() if k != "name"})
    return {"risk": risk, "timing": timing, "focus": focus or {"mode": "all"}}


def test_every_step_and_preset_is_legal_on_every_level():
    for level in PROFILES:
        for i in range(5):
            r = tighten(PROFILES[level], STRATS[level], body_for(i, level), version=1, universe=UNI, themes=THEMES)
            assert is_tightening(r, PROFILES[level], STRATS[level])
        for key, pr in P["presets"].items():
            b = body_for(pr["boldness"], level, pr["hours"], pr["caution"], pr["focus"])
            r = tighten(PROFILES[level], STRATS[level], b, version=1, universe=UNI, themes=THEMES)
            assert is_tightening(r, PROFILES[level], STRATS[level]), key


def test_unclamped_step_above_the_level_is_rejected():
    raw = {k: v for k, v in P["boldness"][4].items() if k != "name"}
    try:
        tighten(PROFILES["balanced"], STRATS["balanced"], {"risk": raw}, version=1, universe=UNI, themes=THEMES)
    except ConfigError:
        return
    raise AssertionError("a Max step above Balanced was accepted")


def test_exclude_limit():
    assert P["limits"]["max_excluded"] == MAX_EXCLUDED
    syms = sorted(UNI)
    ok = {"focus": {"mode": "all", "exclude": syms[:10]}}
    tighten(PROFILES["balanced"], STRATS["balanced"], ok, version=1, universe=UNI, themes=THEMES)
    try:
        tighten(PROFILES["balanced"], STRATS["balanced"], {"focus": {"mode": "all", "exclude": syms[:11]}},
                version=1, universe=UNI, themes=THEMES)
    except ConfigError:
        return
    raise AssertionError("11 exclusions accepted")


def test_reference_symbols_cannot_be_excluded():
    for ref in ["SPY", "XLK", "XLF"]:
        try:
            tighten(PROFILES["balanced"], STRATS["balanced"], {"focus": {"exclude": [ref]}}, version=1, universe=UNI,
                    themes=THEMES)
        except ConfigError:
            continue
        raise AssertionError(ref)


def test_json_is_valid():
    json.loads((ROOT / "apps" / "web" / "src" / "data" / "presets.json").read_text())
