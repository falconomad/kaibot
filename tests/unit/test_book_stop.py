"""Decision 17 (M11 ticket 2.10, #461): the multi-day book's stop as the Gate C bootstrap band, behind a switch that
is `fixed` (today's stop) in the repository. Written before any engine wiring (CLAUDE.md: tests first for a risk
rule): the fixed mode is `gate.book_run_ended` on every input, the band mode behaves as the record says, and
nothing live reads the new file yet."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.config import ROOT
from kaibot.risk import book_stop as bs
from kaibot.risk.gate import BOOK_HARD_CEILINGS, BookLimits, book_run_ended

hyp = pytest.importorskip("hypothesis")
st = hyp.strategies

LIM = BookLimits(run_drawdown_end_pct=27.0)
CFG = bs.load()
BAND = replace(CFG, mode="band")
TABLE = bs.BandTable("index_core", "aggressive", (5, 20, 60, 252, 1000), (0.04, 0.08, 0.13, 0.22, 0.30),
                     (0.06, 0.11, 0.18, 0.30, 0.45))


# ---------------------------------------------------------------- the switch and today's stop


def test_the_repository_keeps_todays_stop():
    assert CFG.mode == "fixed" and CFG.tables == {}
    assert (CFG.retire_quantile, CFG.alert_quantile, CFG.ceiling_pct, CFG.min_sessions) == (0.99, 0.95, 27.0, 20)
    assert BOOK_HARD_CEILINGS.run_drawdown_end_pct == 27.0  # the fixed line's ceiling is unchanged
    text = (ROOT / "docs" / "decisions" / "17_book_stop.md").read_text()
    for phrase in ("**proposed**", "mode: fixed", "99%", "95%", "2,000 paths", "40%", "keep_index_core",
                   "paused for review", "Transition"):
        assert phrase in text, phrase


def test_no_engine_code_reads_the_band_yet():
    """The wiring is a later two-key change (decision 17 section 7): today only research code imports it. Every
    file under src/ is checked (a dynamic import or a registry would name the module), and the module is not
    imported by importing the engine."""
    own = ROOT / "src" / "kaibot" / "risk" / "book_stop.py"
    users = [p.relative_to(ROOT) for p in (ROOT / "src").rglob("*") if p.is_file() and p != own
             and p.suffix in (".py", ".yaml", ".yml", ".json", ".toml", ".cfg") and "book_stop" in p.read_text()]
    assert users == []
    import subprocess
    code = ("import sys, kaibot.engine.core, kaibot.engine.__main__, kaibot.risk.gate; "
            "print('kaibot.risk.book_stop' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                         env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}, check=True)
    assert out.stdout.strip() == "False"


def test_a_missing_or_bad_config_fails_closed_to_the_fixed_stop(tmp_path: Path):
    good = bs.DEFAULT_PATH.read_text()
    bad = {"missing": None, "empty": "", "not yaml": "mode: [fixed",
           "mode off": good.replace("mode: fixed", "mode: off"), "no band": "version: 1\nmode: band\n",
           "loose ceiling": good.replace("  ceiling_pct: 27", "  ceiling_pct: 40")}
    for name, text in bad.items():
        f = tmp_path / f"{name}.yaml"
        if text is not None:
            f.write_text(text)
        cfg, why = bs.load_or_fixed(f)
        assert cfg.mode == "fixed" and why, name
        assert bs.verdict(100.0, 73.0, LIM, cfg=cfg, table=TABLE, sessions=60).end, name  # the 27% line stands
        assert bs.end_action(cfg, True) == "all", name
    assert bs.load_or_fixed() == (CFG, None)


def test_any_mode_but_band_is_the_fixed_line_even_with_a_table():
    for mode in ("off", "", "Band", None):
        v = bs.verdict(100.0, 73.0, LIM, cfg=replace(CFG, mode=mode), table=TABLE, sessions=1000)
        assert v.mode == "fixed" and v.end


def test_the_band_bootstrap_is_gate_cs():
    import yaml
    gate_c = yaml.safe_load((ROOT / "config" / "gate_v2.yaml").read_text())["gate_c"]
    b = gate_c["bootstrap"]
    assert CFG.bootstrap == {k: b[k] for k in ("method", "mean_block_sessions", "runs", "seed")}
    assert (CFG.alert_quantile, CFG.retire_quantile) == (gate_c["drawdown_band"], gate_c["retirement"]["drawdown_band"])


@hyp.settings(max_examples=500, deadline=None)
@hyp.given(peak=st.floats(1, 1e7), fall=st.floats(-0.5, 1.0), end=st.floats(1, 27), dd=st.floats(0, 1),
           sessions=st.integers(0, 3000), with_table=st.booleans())
def test_property_the_fixed_mode_is_book_run_ended_on_every_input(peak, fall, end, dd, sessions, with_table):
    lim = BookLimits(run_drawdown_end_pct=end)
    equity = peak * (1 - fall)
    v = bs.verdict(peak, equity, lim, cfg=CFG, table=TABLE if with_table else None, record_max_drawdown=dd,
                   sessions=sessions)
    assert v.end is book_run_ended(peak, equity, lim) and v.mode == "fixed" and not v.alert


# ---------------------------------------------------------------- the band


def test_the_band_ends_at_the_99_line_of_the_records_length_and_alerts_at_95():
    # 60 sessions: 95% line 13%, 99% line 18%
    v = bs.verdict(100.0, 88.0, LIM, cfg=BAND, table=TABLE, sessions=60)
    assert (v.end, v.alert, v.horizon) == (False, False, 60) and v.drawdown == pytest.approx(0.12)
    v = bs.verdict(100.0, 86.0, LIM, cfg=BAND, table=TABLE, sessions=60)
    assert (v.end, v.alert) == (False, True)
    v = bs.verdict(100.0, 82.0, LIM, cfg=BAND, table=TABLE, sessions=60)
    assert v.end and v.line == pytest.approx(0.18) and "99% line" in v.why


def test_the_band_counts_the_records_worst_fall_not_only_todays():
    v = bs.verdict(100.0, 99.0, LIM, cfg=BAND, table=TABLE, sessions=60, record_max_drawdown=0.19)
    assert v.end and v.drawdown == pytest.approx(0.19)


def test_early_in_a_run_the_band_is_tighter_than_the_line_and_late_it_is_wider():
    """The point of the record: a 15% fall in the first month is outside the test; 28% in year four is not."""
    assert bs.verdict(100.0, 85.0, LIM, cfg=BAND, table=TABLE, sessions=20).end  # 99% line at 20: 11%
    assert not bs.verdict(100.0, 85.0, LIM, cfg=CFG, table=TABLE, sessions=20).end  # 27% line
    tight = BookLimits(run_drawdown_end_pct=13.0)  # Careful: the band at 1000 sessions is wider than 13% ...
    assert not bs.verdict(100.0, 80.0, tight, cfg=BAND, table=TABLE, sessions=1000).end
    assert bs.verdict(100.0, 80.0, tight, cfg=CFG, table=TABLE, sessions=1000).end
    assert bs.verdict(100.0, 72.0, LIM, cfg=BAND, table=TABLE, sessions=1000).end  # ... but never past 27%


def test_horizons_between_and_beyond_the_table_and_the_minimum_record():
    assert TABLE.at(59) == (0.08, 0.11) and TABLE.at(60) == (0.13, 0.18)  # the stricter neighbour below
    assert TABLE.at(5000) == (0.30, 0.45) and TABLE.at(1) == (0.04, 0.06)
    v = bs.verdict(100.0, 89.0, LIM, cfg=BAND, table=TABLE, sessions=3)  # 3 sessions: judged at 20 (11%), not 5
    assert v.horizon == 20 and v.end and v.line == pytest.approx(0.11)
    assert not bs.verdict(100.0, 90.0, LIM, cfg=BAND, table=TABLE, sessions=3).end


def test_the_ceiling_caps_the_line():
    v = bs.verdict(100.0, 73.0, LIM, cfg=BAND, table=TABLE, sessions=1000)
    assert v.end and v.line == pytest.approx(0.27) and "(ceiling)" in v.why
    low = replace(BAND, ceiling_pct=20.0)
    assert bs.verdict(100.0, 79.0, LIM, cfg=low, table=TABLE, sessions=1000).end
    loose = replace(BAND, ceiling_pct=40.0)  # built around load(), which refuses it: still capped at 27%
    v = bs.verdict(100.0, 70.0, LIM, cfg=loose, table=TABLE, sessions=1000)
    assert v.end and v.line == pytest.approx(BOOK_HARD_CEILINGS.run_drawdown_end_pct / 100)


@hyp.settings(max_examples=500, deadline=None)
@hyp.given(fall=st.floats(0, 1), dd=st.floats(0, 1), sessions=st.integers(0, 3000), ceil=st.floats(1, 100),
           q=st.lists(st.floats(0.001, 1), min_size=1, max_size=6))
def test_property_the_band_never_lets_a_loss_run_past_the_hard_ceiling(fall, dd, sessions, ceil, q):
    q99 = tuple(sorted(q))
    t = bs.BandTable("r", "l", tuple(range(1, len(q99) + 1)), q99, q99)
    hard = BOOK_HARD_CEILINGS.run_drawdown_end_pct / 100
    v = bs.verdict(100.0, 100.0 * (1 - fall), LIM, cfg=replace(BAND, ceiling_pct=ceil), table=t,
                   record_max_drawdown=dd, sessions=sessions)
    assert v.line <= hard + 1e-12
    if max(fall, dd) >= hard:
        assert v.end


def test_non_finite_inputs_get_the_fixed_line_never_no_stop():
    nan, inf = float("nan"), float("inf")
    for kw in ({"record_max_drawdown": nan}, {"record_max_drawdown": inf}, {"sessions": 60.5}, {"sessions": True}):
        v = bs.verdict(100.0, 73.0, LIM, cfg=BAND, table=TABLE, **{"sessions": 60, **kw})
        assert v.mode == "fixed" and v.end and "not a finite" in v.why, kw
    for peak, eq in ((nan, 73.0), (100.0, nan), (inf, 73.0)):  # exactly today's answer on the same inputs
        v = bs.verdict(peak, eq, LIM, cfg=BAND, table=TABLE, sessions=60)
        assert v.mode == "fixed" and v.end is book_run_ended(peak, eq, LIM)


def test_a_band_mode_without_a_table_keeps_the_fixed_line():
    v = bs.verdict(100.0, 73.0, LIM, cfg=BAND, table=None, sessions=60)
    assert v.mode == "fixed" and v.end and "no band table" in v.why


@hyp.settings(max_examples=300, deadline=None)
@hyp.given(a=st.floats(0, 0.6), b=st.floats(0, 0.6), sessions=st.integers(0, 2000))
def test_property_a_deeper_fall_never_ends_later(a, b, sessions):
    lo, hi = sorted((a, b))
    v_lo = bs.verdict(100.0, 100.0 * (1 - lo), LIM, cfg=BAND, table=TABLE, sessions=sessions)
    v_hi = bs.verdict(100.0, 100.0 * (1 - hi), LIM, cfg=BAND, table=TABLE, sessions=sessions)
    assert v_hi.end >= v_lo.end and v_hi.alert >= v_lo.alert
    assert not v_lo.end or lo >= v_lo.line - 1e-9  # never below the 99% line


def test_the_end_keeps_the_index_core_only_in_band_mode():
    assert bs.end_action(CFG, True) == "all" and bs.end_action(BAND, False) == "all"
    assert bs.end_action(BAND, True) == "sleeves"


# ---------------------------------------------------------------- the tables


@pytest.mark.parametrize("kw, why", [
    ({"horizons": ()}, "one 95%"),
    ({"horizons": (20, 5, 60, 252, 1000)}, "strictly increasing"),
    ({"q99": (0.06, 0.11, 0.18, 1.2, 1.3)}, "between 0 and 1"),
    ({"q95": (0.04, 0.12, 0.13, 0.22, 0.30)}, "95% line is above"),
    ({"q99": (0.06, 0.11, 0.18, 0.17, 0.45), "q95": (0.04, 0.08, 0.13, 0.16, 0.30)}, "falls as the horizon"),
    ({"q99": (0.0, 0.0, 0.0, 0.0, 0.0), "q95": (0.0, 0.0, 0.0, 0.0, 0.0)}, "degenerate"),
])
def test_a_malformed_table_is_refused(kw, why):
    base = TABLE.to_dict()
    base.update({k: list(v) for k, v in kw.items()})
    with pytest.raises(ValueError, match=why):
        bs.BandTable.from_dict(base)


def test_a_committed_table_is_read_and_must_be_for_its_rule_and_level(tmp_path: Path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps(TABLE.to_dict()))
    cfg = replace(BAND, tables={"index_core": {"aggressive": str(f), "balanced": str(f)}})
    assert bs.table_for(cfg, "index_core", "aggressive", root=tmp_path) == TABLE
    with pytest.raises(ValueError, match="not of index_core at balanced"):
        bs.table_for(cfg, "index_core", "balanced", root=tmp_path)
    assert bs.table_for(cfg, "trend_momentum", "aggressive", root=tmp_path) is None


def test_a_bad_config_is_refused(tmp_path: Path):
    good = bs.DEFAULT_PATH.read_text()
    for bad, why in ((good.replace("mode: fixed", "mode: loose"), "mode must be"),
                     (good.replace("alert_quantile: 0.95", "alert_quantile: 0.995"), "alert_quantile"),
                     (good.replace("  ceiling_pct: 27", "  ceiling_pct: 140"), "ceiling_pct"),
                     (good.replace("  ceiling_pct: 27", "  ceiling_pct: 27.5"), "loosens the hard ceiling")):
        f = tmp_path / "b.yaml"
        f.write_text(bad)
        with pytest.raises(ValueError, match=why):
            bs.load(f)


# ---------------------------------------------------------------- the script that builds a table


def test_the_band_script_builds_a_monotone_seeded_table_on_the_research_window_only(tmp_path: Path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import book_stop_bands as script

    idx = pd.bdate_range("2017-01-02", "2026-11-06")  # reaches into the hold-out on purpose
    r = np.random.default_rng(461).normal(0.0004, 0.01, len(idx))
    eq = 100_000 * np.cumprod(1 + r)
    start = np.r_[100_000, eq[:-1]]
    pd.DataFrame({"date": idx.date, "start": start, "end": eq}).to_csv(tmp_path / "daily.csv", index=False)
    small = replace(CFG, bootstrap={**CFG.bootstrap, "runs": 200})
    a, src = script.build(script.returns_of(pd.read_csv(tmp_path / "daily.csv")), "index_core", "aggressive", small)
    b, _ = script.build(script.returns_of(pd.read_csv(tmp_path / "daily.csv")), "index_core", "aggressive", small)
    assert a == b  # seeded
    assert src["window"] == ["2017-05-01", "2024-12-31"] and src["sessions_in_holdout_dropped"] > 400
    assert list(a.q99) == sorted(a.q99) and all(x <= y for x, y in zip(a.q95, a.q99, strict=True))
    assert a.horizons[-1] == src["sessions"] and 0.0 < a.q99[0] < a.q99[-1] < 1.0
    assert src["bootstrap"]["seed"] == 15


def test_the_band_script_refuses_a_run_without_variance_and_a_short_one():
    sys.path.insert(0, str(ROOT / "scripts"))
    import book_stop_bands as script

    idx = pd.bdate_range("2018-01-02", periods=300)
    small = replace(CFG, bootstrap={**CFG.bootstrap, "runs": 50})
    with pytest.raises(ValueError, match="degenerate"):
        script.build(pd.Series(0.0, index=idx), "r", "aggressive", small)
    with pytest.raises(SystemExit, match="too few"):
        script.build(pd.Series(0.001, index=idx[:40]), "r", "aggressive", small)
