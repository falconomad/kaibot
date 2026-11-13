"""M5: the SQL side of the config panel (migration 0011) mirrors the Python side.

1. Always: the level limits, themes and universe seeded in 0011 match config/profiles.yaml and
   config/universe_seed.yaml (no Postgres needed).
2. When a scratch Postgres is reachable (KAIBOT_TEST_PG="-h /tmp -p 55432 -U postgres -d m5", with 0011 applied):
   SQL `kaibot.config_check` and Python `userconfig.tighten` agree on random configs. Skipped otherwise (CI).
   With migration 0014 applied too, the same fuzz covers focus.custom, including the rule that a custom list is
   accepted only in Only mode."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kaibot.engine.config import ROOT

SQL = (ROOT / "migrations" / "0011_m5_config.sql").read_text()
PROF = yaml.safe_load((ROOT / "config" / "profiles.yaml").read_text())
UNI = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())


def _names(arr: str) -> list[str]:
    return [s.strip(" '") for s in arr.split(",") if s.strip(" '")]


def level_strategies_after_migrations() -> dict[str, list[str]]:
    """The level -> strategies of 0011's seed with every later `update kaibot.level_limit set strategies` applied in
    file order (0016 retired SPY late momentum, #128)."""
    rows = re.findall(r"\('(\w+)', ([\d.]+), (\d+), ([\d.]+), (\d+), ([\d.]+), ([\d.]+), array\[([^\]]*)\]\)", SQL)
    out = {r[0]: _names(r[7]) for r in rows}
    for f in sorted((ROOT / "migrations").glob("*.sql")):
        if f.name <= "0011":
            continue
        upd = r"update kaibot\.level_limit set strategies = array\[([^\]]*)\] where level = '(\w+)'"
        for arr, lv in re.findall(upd, f.read_text()):
            out[lv] = _names(arr)
    return out


def test_level_limits_match_profiles():
    rows = re.findall(r"\('(\w+)', ([\d.]+), (\d+), ([\d.]+), (\d+), ([\d.]+), ([\d.]+), array\[([^\]]*)\]\)", SQL)
    got = {r[0]: r for r in rows}
    assert set(got) == set(PROF["levels"])
    for name, lv in PROF["levels"].items():
        r = got[name]
        assert float(r[1]) == lv["risk_per_trade_pct"] and int(r[2]) == lv["max_positions"]
        assert float(r[3]) == lv["max_position_pct"] and int(r[4]) == lv["max_entries_per_day"]
        assert float(r[5]) == lv["daily_stop_pct"] and float(r[6]) == lv["run_drawdown_end_pct"]
    assert level_strategies_after_migrations() == {k: lv["strategies"] for k, lv in PROF["levels"].items()}


def test_sql_strategy_registry_is_the_python_registry():
    """S76 (#116): the SQL validator reads its strategy names from kaibot.strategy_registry; the rows seeded by the
    latest migration that writes it must be exactly strategies/registry.py."""
    from kaibot.strategies import registry
    from kaibot.strategies.base import Mode

    files = [f for f in sorted((ROOT / "migrations").glob("*.sql")) if "into kaibot.strategy_registry" in f.read_text()]
    text = files[-1].read_text()
    block = text[text.index("insert into kaibot.strategy_registry"):]
    rows = re.findall(r"\('(\w+)', (\d+), '(\w+)', '(\w+)', (true|false), (true|false)\)", block[: block.index(";")])
    want = [(s.name, str(i + 1), str(s.mode), str(s.holding), str(s.mode is Mode.LIVE).lower(),
             str(s.name in registry.RETIRED).lower()) for i, s in enumerate(registry.STRATEGIES)]
    assert rows == want
    assert "'spy_late_momentum'" not in text[text.index("create or replace function kaibot.config_check(") :]


def test_themes_and_universe_match_yaml():
    rows = re.findall(r"\('(\w+)', '([^']+)', array\[([^\]]*)\]\)", SQL)
    themes = {k: (label, [s.strip(" '") for s in syms.split(",")]) for k, label, syms in rows}
    assert {k: (v["label"], v["symbols"]) for k, v in UNI["themes"].items()} == themes
    block = SQL[SQL.index("insert into kaibot.config_universe"):]
    uni = re.findall(r"'([A-Z]+)'", block[: block.index(";")])
    assert sorted(uni) == sorted(s for v in UNI["sectors"].values() for s in v)


PG = os.environ.get("KAIBOT_TEST_PG")


@pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0011 applied")
@settings(max_examples=1, deadline=None, suppress_health_check=list(HealthCheck), database=None)
@given(st.just(None))
def test_sql_and_python_validators_agree(_):
    import test_userconfig as t
    from hypothesis import find  # noqa: F401  (keeps hypothesis imported for the generator below)

    cases = []

    @settings(max_examples=1500, deadline=None, suppress_health_check=list(HealthCheck), database=None)
    @given(level=st.sampled_from(sorted(t.PROFILES)), b=t.body)
    def gen(level, b):
        cases.append((level, b))

    @settings(max_examples=500, deadline=None, suppress_health_check=list(HealthCheck), database=None)
    @given(level=st.sampled_from(sorted(t.PROFILES)), b=t.custom_body)
    def gen_custom(level, b):  # well-formed custom lists in every mode (migration 0014)
        cases.append((level, b))

    gen()
    gen_custom()
    rows = []
    for lv, b in cases:
        s = json.dumps(b, allow_nan=True)
        if "NaN" in s or "Infinity" in s or "\\u0000" in s:
            continue  # jsonb cannot hold them (Python rejects NaN/inf anyway)
        try:
            json.dumps(b, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            continue  # lone surrogates: not valid in jsonb either
        try:
            t.T(lv, b)
            ok = True
        except t.ConfigError:
            ok = False
        rows.append((lv, s, ok))
    script = ["create temp table c(i int, lv text, b jsonb);",
              "copy c from stdin with (format csv, quote '\x01', delimiter '\x02');"]
    script += [f"{i}\x02{lv}\x02{s}" for i, (lv, s, _) in enumerate(rows)] + ["\\.",
               "select i, kaibot.config_check(lv, b) is null from c order by i;"]
    out = subprocess.run(["psql", *shlex.split(PG), "-tA", "-F", ",", "-v", "ON_ERROR_STOP=1"], input="\n".join(script),
                         capture_output=True, text=True, check=True)
    sql = {int(a): b == "t" for a, b in (line.split(",") for line in out.stdout.split() if "," in line)}
    bad = [(rows[i][0], rows[i][1]) for i in range(len(rows)) if sql[i] != rows[i][2]]
    print(f"parity cases compared: {len(rows)}")
    assert not bad, bad[:5]
    assert sum(r[2] for r in rows) > 100  # the comparison covered accepted configs, not just rejections
    # S68 (migration 0014): the fuzz also covered custom lists on both sides of the Only-mode rule
    foci = [(json.loads(s).get("focus"), ok) for _, s, ok in rows]
    cust = [(f, ok) for f, ok in foci if isinstance(f, dict) and "custom" in f]
    assert sum(ok for _, ok in cust) >= 50, "too few accepted custom lists (Only mode) in the comparison"
    assert all(f.get("mode") == "only" for f, ok in cust if ok), "a custom list was accepted outside Only mode"
    assert sum(1 for f, _ in cust if f.get("mode", "all") in ("all", "prefer")) >= 50
