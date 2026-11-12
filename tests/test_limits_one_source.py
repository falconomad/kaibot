"""#122 (audit H6): the level limits live in three places and must agree.

- `config/profiles.yaml` levels (what the engine loads, risk/profiles.py),
- the SQL table `kaibot.level_limit` as the migrations leave it (what the config panel validates against),
- `HARD_CEILINGS` in risk/gate.py (what no profile or user setting may loosen).

Every level is within the ceilings on both sides, the SQL rows equal the YAML levels field by field, and the loosest
level (Hunter) is exactly the ceiling, so the ceiling is not a fourth number that can drift on its own."""

from __future__ import annotations

import re

import yaml

from kaibot.engine.config import ROOT
from kaibot.risk.gate import HARD_CEILINGS
from kaibot.risk.profiles import load_profiles

PROF = yaml.safe_load((ROOT / "config" / "profiles.yaml").read_text())
ROW = re.compile(r"\('(\w+)', ([\d.]+), (\d+), ([\d.]+), (\d+), ([\d.]+), ([\d.]+), array\[[^\]]*\]\)")
# level_limit columns in table order (migrations/0011) -> the RiskPolicy field each one sets (risk/profiles.py)
COLUMNS = {"risk_per_trade_pct": "risk_per_trade_pct", "max_positions": "max_positions",
           "max_position_pct": "max_position_pct", "max_entries_per_day": "max_entries_per_day",
           "daily_stop_pct": "max_daily_loss_pct", "run_drawdown_end_pct": "run_drawdown_end_pct"}


def sql_level_limits() -> dict[str, dict[str, float]]:
    """kaibot.level_limit after every migration: 0011 seeds it; a later `update kaibot.level_limit set <column> = <n>
    where level = '<x>'` overrides one number (none today; 0016 only changes strategies)."""
    files = sorted((ROOT / "migrations").glob("*.sql"))
    seeds = [f for f in files if "insert into kaibot.level_limit" in f.read_text()]
    assert seeds, "no migration seeds kaibot.level_limit"
    out: dict[str, dict[str, float]] = {}
    for f in files[files.index(seeds[0]):]:
        text = f.read_text()
        if "insert into kaibot.level_limit" in text:
            block = text[text.index("insert into kaibot.level_limit"):]
            for r in ROW.findall(block[: block.index(";")]):
                out[r[0]] = {c: float(v) for c, v in zip(COLUMNS, r[1:], strict=True)}
        for col, val, lv in re.findall(r"update kaibot\.level_limit set (\w+) = ([\d.]+) where level = '(\w+)'", text):
            out[lv][col] = float(val)
    return out


def test_sql_level_limit_equals_profiles_yaml():
    sql = sql_level_limits()
    assert set(sql) == set(PROF["levels"])
    for name, lv in PROF["levels"].items():
        assert sql[name] == {c: float(lv[c]) for c in COLUMNS}, name


def test_every_level_is_within_the_hard_ceilings_on_both_sides():
    for side, levels in (("profiles.yaml", {k: {c: float(v[c]) for c in COLUMNS} for k, v in PROF["levels"].items()}),
                         ("level_limit", sql_level_limits())):
        for name, lv in levels.items():
            for col, field in COLUMNS.items():
                assert lv[col] <= getattr(HARD_CEILINGS, field), f"{side} {name}.{col} loosens HARD_CEILINGS"
    assert PROF["all_levels"]["max_quote_age_s"] <= HARD_CEILINGS.max_quote_age_s
    assert PROF["all_levels"]["allow_short"] is False and HARD_CEILINGS.allow_short is False


def test_the_loosest_level_is_the_ceiling():
    pols = load_profiles()
    for field in [*COLUMNS.values(), "max_open_risk_pct", "max_quote_age_s"]:
        assert max(getattr(p, field) for p in pols.values()) == getattr(HARD_CEILINGS, field), field
    assert pols["aggressive"] == HARD_CEILINGS  # Hunter is the ceiling, field for field
