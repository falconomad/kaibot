"""Gate v3.1 B7 (decision 18 section 10, M17 ticket 6.6, #706): a book with a return sleeve is judged against its
budget-matched control book (the same core, the sleeve's budget held in SPY, not idle cash), on five pre-registered
start offsets, with the spread reported. Written with the change; the ten years are on the owner's Mac, so the
engine runs here are on the committed fixture."""

from __future__ import annotations

import json
import re
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import prereg as prereg_mod
from kaibot.risk import index_core as ic
from kaibot.strategies import momentum as mom
from kaibot.strategies.index_core import ControlBook, IndexCore, OnCore, control_weight

sys.path.insert(0, str(ROOT / "scripts"))
import gate_v3_rescore as rescore  # noqa: E402
import index_core_book as script  # noqa: E402

GATE = g3.GateV3.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2024-12-31")


def series(drift: float, seed: int = 0, idx=IDX) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(drift / 252 + rng.normal(0, 0.008, len(idx)), index=idx)


def pairs(book_drift: float, control_drift: float, offsets=(0, 5, 10, 15, 20)) -> dict:
    out = {}
    for k in offsets:
        idx = IDX[k:]
        noise = series(0.0, seed=k, idx=idx)
        out[k] = (noise + book_drift / 252, noise + control_drift / 252)
    return out


# ---------------------------------------------------------------- the pre-registered mark


def test_b7_is_pre_registered_in_the_gate_and_pinned_by_decision_18():
    m = GATE.cfg["book"]["b7_control_book"]
    assert m["control"] == "sleeve_budget_in_spy" and m["statistic"] == "cagr_points_over_control"
    assert m["start_offsets_sessions"] == [0, 5, 10, 15, 20] and m["offsets_min"] == 5 and m["median_above"] == 0.0
    assert g3.control_offsets(GATE) == (0, 5, 10, 15, 20)
    text = (ROOT / "docs" / "decisions" / "18_gate_v3_1.md").read_text()
    assert "## 10. Ticket 6.6" in text and "0, 5, 10, 15 and 20 sessions" in text and "**median** offset" in text


@pytest.mark.parametrize("change, match", [
    ({"start_offsets_sessions": [0, 5, 10, 15]}, "offsets_min"),
    ({"offsets_min": 4, "start_offsets_sessions": [0, 5, 10, 15]}, "offsets_min must be at least 5"),
    ({"start_offsets_sessions": [5, 10, 15, 20, 25]}, "include 0"),
    ({"start_offsets_sessions": [0, 5, 5, 10, 15]}, "distinct"),
    ({"control": "idle_cash"}, "sleeve_budget_in_spy"),
])
def test_the_config_refuses_a_weaker_b7(change, match):
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["book"]["b7_control_book"].update(change)
    with pytest.raises(ValueError, match=match):
        g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())


def test_a_book_with_no_return_sleeve_holds_b7_and_one_with_a_sleeve_needs_its_control():
    assert g3.b7(GATE, [], None)["met"] is True
    m = g3.b7(GATE, ["trend_momentum_sleeve"], None)
    assert m["met"] is None and "no control book" in m["why"]


def test_b7_is_judged_at_the_median_offset_and_prints_the_spread():
    better = g3.b7(GATE, ["s"], pairs(0.02, 0.0))
    assert better["met"] is True
    sp = better["numbers"]["cagr_points_over_control"]
    assert sp["n"] == 5 and sp["median"] > 0 and sp["min"] <= sp["median"] <= sp["max"] and sp["sd"] >= 0
    assert set(better["numbers"]["offsets"]) == {0, 5, 10, 15, 20}
    assert better["numbers"]["offsets"][20]["first_session"] == IDX[20].date().isoformat()
    # the same budget in SPY doing as well (or better) fails: the median must be above 0
    assert g3.b7(GATE, ["s"], pairs(0.0, 0.0))["met"] is False
    worse = g3.b7(GATE, ["s"], pairs(0.0, 0.02))
    assert worse["met"] is False and "did as well" in worse["why"]


def test_one_good_start_cannot_carry_b7():
    p = pairs(0.0, 0.01)
    noise = series(0.0, seed=99)
    p[0] = (noise + 0.10 / 252, noise)  # offset 0 (the judged run) far ahead, the other four behind
    m = g3.b7(GATE, ["s"], p)
    assert m["numbers"]["offsets"][0]["cagr_points_over_control"] > 5 and m["met"] is False


def test_fewer_offsets_than_pre_registered_is_unknown_and_extra_ones_are_not_counted():
    four = {k: v for k, v in pairs(0.02, 0.0).items() if k != 20}
    m = g3.b7(GATE, ["s"], {**four, 7: pairs(0.02, 0.0, offsets=(7,))[7]})
    assert m["met"] is None and "4 of the pre-registered start offsets" in m["why"]
    assert m["numbers"]["offsets_not_pre_registered"] == [7]


def test_b7_runs_on_the_research_window_only():
    long = pd.bdate_range("2017-05-01", "2026-06-30")
    rng = np.random.default_rng(3)
    base = pd.Series(rng.normal(0, 0.008, len(long)), index=long)
    hold = long > pd.Timestamp("2024-12-31")
    # the book wins only in the hold-out: B7 must not see it
    book = base + np.where(hold, 0.01, 0.0)
    m = g3.b7(GATE, ["s"], {k: (book.iloc[k:], base.iloc[k:]) for k in (0, 5, 10, 15, 20)})
    assert m["met"] is False
    assert all(p["sessions"] <= len(IDX) for p in m["numbers"]["offsets"].values())


def test_evaluate_book_adds_b7_and_it_can_only_make_a_pass_harder():
    acc = g3.Account(series(0.12, 1), series(0.10, 2), universe="point_in_time")
    kw = dict(book="index_core_trend_momentum", account=acc, sleeves={"trend_momentum_sleeve": "fail"},
              ran_budgets={"trend_momentum_sleeve": 10.0})
    without = g3.evaluate_book(GATE, **kw)
    assert without["marks"]["b7"]["met"] is None and without["verdict"] == "fail"
    good = g3.evaluate_book(GATE, **kw, control_books=pairs(0.02, 0.0))
    bad = g3.evaluate_book(GATE, **kw, control_books=pairs(0.0, 0.02))
    assert good["marks"]["b7"]["met"] is True and bad["marks"]["b7"]["met"] is False
    # the other marks do not move with the control book
    assert {k: v["met"] for k, v in good["marks"].items() if k != "b7"} == \
        {k: v["met"] for k, v in bad["marks"].items() if k != "b7"}
    core = g3.evaluate_book(GATE, book="index_core", account=acc, sleeves={}, ran_budgets={})
    assert core["marks"]["b7"]["met"] is True and core["marks"]["b7"]["numbers"]["return_sleeves"] == []
    text = g3.render_book(good)
    assert "B7. The book beats its control book" in text and "Spread over 5 offsets" in text


# ---------------------------------------------------------------- the control book through the engine


def test_control_weight_puts_the_whole_budget_in_spy_beside_the_cores_own_weight():
    assert control_weight(1.0, 80, 10) == pytest.approx(1.0)
    assert control_weight(0.5, 80, 10) * 90 == pytest.approx(0.5 * 80 + 10)
    assert control_weight(0.0, 27, 10) * 37 == pytest.approx(10)
    with pytest.raises(ValueError):
        control_weight(0.5, 80, 0)


def test_the_control_builder_holds_the_core_cap_plus_the_budget():
    cfg = ic.load()
    for cap in (27.0, 80.0):
        c = ControlBook.of(script.book_of(), cap)
        b = script.control_builder(c, cfg)
        assert c.total_cap_pct == cap + 10.0 == b.limits.max_index_pct and b.index_symbol == "SPY"
        assert c.budget_pct == 10.0 and c.sleeve is script.book_of().sleeve


@pytest.fixture(scope="module")
def fixture_data():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, info = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    return data, i0, len(data.dates) - 1


def test_the_control_book_holds_only_spy_at_core_weight_plus_budget(fixture_data):
    from kaibot.engine.daily import run_daily
    from kaibot.risk.profiles import load_profiles

    data, i0, i1 = fixture_data
    profiles, cfg = load_profiles(), ic.load()
    book = OnCore(mom.VERSIONS["trend_momentum"], IndexCore())
    ctl = ControlBook.of(book, 27.0)
    pb = script.control_builder(ctl, cfg)
    plans = []
    real = type(pb).build

    def build(self, targets, current, market, **kw):
        p = real(self, targets, current, market, **kw)
        plans.append((targets, p, market, kw["equity"]))
        return p

    type(pb).build = build
    try:
        run = run_daily(ctl, data, profiles["aggressive"], i0, i1, builder=pb)
    finally:
        type(pb).build = real
    assert plans and not run.ledger.overdrafts and {f["symbol"] for f in run.fills} == {"SPY"}
    for targets, p, market, eq in plans:
        index = sum(q * market[s].px for (_, s), q in p.targets.items() if s == "SPY")
        assert index <= 0.37 * eq + 1e-6
        for t in targets:
            w = t.features["core_weight"]
            # the whole budget is in SPY: never below 10% of equity, less the 10% band and a share's rounding
            assert t.weights["SPY"] == pytest.approx(control_weight(w, 27.0, 10.0))
            assert index >= 0.9 * (w * 0.27 + 0.10) * eq - market["SPY"].px - 1e-6


def test_the_book_script_runs_the_control_at_five_offsets_and_counts_no_new_configuration(tmp_path):
    trials = tmp_path / "trials.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "out"), "--trials",
                       str(trials), "--control-runs", "20"])
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    by = {r["label"].removeprefix("engine "): r for r in rows}
    for k in (0, 5, 10, 15, 20):
        assert by[f"control|{k}"]["n_trials"] == 0 and by[f"control|{k}"]["params"]["part"] == "control"
        assert by[f"control|{k}"]["params"]["start_offset_sessions"] == k
        if k:
            assert by[f"book_offset|{k}"]["n_trials"] == 0
    assert sum(r["n_trials"] for r in rows) == 6  # still the judged book and its five neighbours
    cb = out["control_book"]
    assert cb["index_cap_pct"] == 37.0 and cb["start_offsets_sessions"] == [0, 5, 10, 15, 20]
    assert cb["pairs"]["0"] == ["book|1x", "control|0"] and cb["pairs"]["20"] == ["book_offset|20", "control|20"]
    b7 = out["gate_v3_book"]["marks"]["b7"]
    assert b7["met"] in (True, False) and b7["numbers"]["cagr_points_over_control"]["n"] == 5
    firsts = [p["first_session"] for p in b7["numbers"]["offsets"].values()]
    assert firsts == sorted(firsts) and len(set(firsts)) == 5
    md = (tmp_path / "out" / f"{out['file'].rsplit('/', 1)[1].removesuffix('.json')}.md").read_text()
    assert "Control book (gate v3.1, B7)" in md

    # the re-score reads the same pairs back from the report's daily files and gets the same B7
    from kaibot.evaluation import results

    f = tmp_path / "out" / out["file"].rsplit("/", 1)[1]
    rep = json.loads(f.read_text())
    got = rescore.control_books(rep, {r.label: r for r in results.rows(f, rep)})
    assert sorted(got) == [0, 5, 10, 15, 20]
    again = g3.b7(GATE, ["trend_momentum_sleeve"], got)
    assert again["numbers"]["cagr_points_over_control"] == pytest.approx(b7["numbers"]["cagr_points_over_control"])


def test_a_report_without_a_control_book_rescores_b7_as_unknown():
    assert rescore.control_books({}, {}) is None
    assert rescore.control_books({"control_book": {"pairs": {}}}, {}) is None


# ---------------------------------------------------------------- the pre-registration


def test_the_book_prereg_declares_b7_in_an_appended_amendment_printed_as_hindsight():
    p = prereg_mod.load("index_core_trend_momentum", ROOT)
    text = (ROOT / "docs" / "prereg" / "index_core_trend_momentum.md").read_text()
    heads = re.findall(r"^## Amendment (\d+)", text, flags=re.M)
    assert heads == [str(i) for i in range(1, len(heads) + 1)] and heads[-1] == "5"
    a5 = text.split("## Amendment 5", 1)[1]
    for phrase in ("10% budget held in SPY", "0, 5, 10, 15 and 20 sessions", "above 0 at the median",
                   "n_trials 0", "index-core-book-80-control-2026-11-26"):
        assert phrase in a5, phrase
    assert p.design_saw_holdout is True  # Amendment 4 still stands
