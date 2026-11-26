"""Gate v3.1, ticket 6.1 (#701, decision 18): book configurations are counted per book idea, B2 needs a deflated
test of the book's Sharpe over SPY's with N = those configurations, and a verdict says "hindsight" when an
amendment of the pre-registration came after the first logged run."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import prereg as pr
from kaibot.evaluation.prereg import Prereg
from kaibot.evaluation.trials import Registry

GATE = g3.GateV3.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2024-12-31")
FINAL = {"engine": "simbroker", "cost_model": "v2", "data_repairs": True, "benchmark": "spy_total_return"}
RECORD = ROOT / "docs" / "decisions" / "18_gate_v3_1.md"


def spy(seed=16) -> pd.Series:
    return pd.Series(np.random.default_rng(seed).normal(0.0005, 0.011, len(IDX)), index=IDX)


def reg(counts: dict[str, int]) -> Registry:
    return Registry(tuple({"trial_id": f"{k}{i}", "strategy": k, "n_trials": 1, "ts": "2026-11-24T10:00:00+00:00"}
                          for k, n in counts.items() for i in range(n)), ("t",), 0)


def gate_with(books: dict) -> g3.GateV3:
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["books"] = books
    return g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())


def entry(idea=None) -> dict:
    out = {"components": ["index_core"], "sleeves": {}, "v3_declared": True}
    return {**out, "idea": idea} if idea else out


def b2(net, s, n):
    return g3.b2(GATE, net, s, intervals=False, configurations=n)


# ---------------------------------------------------------------- the record and the config


def test_the_record_pins_the_config_hash_and_cites_the_permission_and_the_audit():
    text = RECORD.read_text()
    sha = hashlib.sha256((ROOT / "config" / "gate_v3.yaml").read_bytes()).hexdigest()
    pinned = re.findall(r"config/gate_v3\.yaml sha256: `([0-9a-f]{64})`", text)
    assert pinned == [sha], "config/gate_v3.yaml changed: update the hash line of decision 18 with its reason"
    assert GATE.sha == sha and GATE.version == "3.1"
    assert "docs/decisions/18_gate_v3_1.md" in GATE.cfg["amended_by"]
    for phrase in ("#600", "25 Nov 2026, 17:11 CET", "full permission for Product to keep working without asking",
                   "Stage 6", "#701", "does not take it"):
        assert phrase in text, phrase


def test_b2_branches_are_unchanged_and_the_chance_test_is_pre_declared():
    from kaibot.evaluation import gate_v2 as g2

    assert GATE.cfg["book"]["b2_spy"] == g2.GateV2.load(ROOT).cfg["gate_b"]["mark_2"]
    assert GATE.cfg["book"]["b2_chance"] == {"statistic": "sharpe_over_spy", "trials": "book_idea",
                                             "variance": "paired_bootstrap", "dsr_min": 0.90}


def test_a_bad_idea_name_fails_loudly():
    with pytest.raises(ValueError, match="idea must be a name"):
        gate_with({"b": entry("Not A Name")})


# ---------------------------------------------------------------- N per book idea


def test_index_core_shows_n_12_from_the_published_registry_counts():
    """The verification of #701: the re-score's registry (25 Nov 2026) gives index_core N = 12."""
    pub = json.loads((ROOT / "docs" / "research" / "results" / "gate_v3_rescore.json").read_text())
    counts = pub["registry"]["by_strategy"]
    r = Registry(tuple({"trial_id": k, "strategy": k, "n_trials": v} for k, v in counts.items()), ("t",), 0)
    assert g3.book_configurations(GATE, "index_core", r) == {"idea": "index_core", "books": ["index_core"],
                                                              "configurations": 12}
    assert g3.book_configurations(GATE, "index_core_trend_momentum", r)["configurations"] == 12


def test_books_of_one_idea_share_n_and_an_unlogged_run_counts():
    gate = gate_with({"core_27": entry("core"), "core_97": entry("core"), "other": entry()})
    r = reg({"core_27": 6, "core_97": 6, "other": 3})
    assert g3.book_configurations(gate, "core_97", r)["configurations"] == 12
    assert g3.book_configurations(gate, "core_27", r, unlogged=6)["configurations"] == 18
    assert g3.book_configurations(gate, "other", r)["configurations"] == 3
    assert g3.book_configurations(gate, "core_27", None)["configurations"] is None


# ---------------------------------------------------------------- the chance test


def test_a_de_levered_spy_meets_the_defensive_branch_and_fails_the_chance_test():
    """70% of SPY: SPY's Sharpe, 70% of its drawdown and, in a low-return draw, a CAGR within 2 points. Gate v3
    passed such a book on B2; gate v3.1 does not, because its Sharpe is no higher than SPY's."""
    s = pd.Series(np.random.default_rng(4).normal(0.0001, 0.011, len(IDX)), index=IDX)
    m = b2(0.7 * s, s, 1)
    assert m["numbers"]["branch"]["defensive"] is True
    assert m["met"] is False and "deflated Sharpe over SPY's" in m["why"]


def test_more_configurations_raise_the_hurdle_and_can_turn_the_chance_test_off():
    s = spy(5)
    rng = np.random.default_rng(7)
    book = 0.6 * s + rng.normal(0.00012, 0.0015, len(IDX))  # a modest Sharpe gain over SPY
    one, many = b2(book, s, 1)["numbers"]["chance"], b2(book, s, 1000)["numbers"]["chance"]
    assert one["hurdle"] == 0.0 < many["hurdle"] and many["dsr"] < one["dsr"]
    assert one["sharpe_over_spy"] == pytest.approx(many["sharpe_over_spy"])


def test_without_n_b2_is_unknown_never_a_pass():
    s = spy(9)
    m = b2(0.6 * s + 0.0003, s, None)
    assert m["met"] is None and "cannot count N" in m["why"]


@pytest.mark.parametrize("seed", range(8))
def test_b2_never_holds_when_gate_v3s_branches_do_not(seed):
    """No retroactive pass: v3.1's B2 holds only where v3's branch test holds."""
    s = spy(100 + seed)
    rng = np.random.default_rng(seed)
    book = rng.uniform(0.3, 1.2) * s + rng.normal(rng.uniform(-0.0003, 0.0006), 0.002, len(IDX))
    for n in (1, 6, 12):
        m = b2(book, s, n)
        if m["met"] is True:
            assert any(m["numbers"]["branch"].values())
        if not any(m["numbers"]["branch"].values()):
            assert m["met"] is False


def test_the_book_report_lists_n_and_the_chance_test():
    s = spy(11)
    acc = g3.Account(0.6 * s + 0.0003, s, costs=pd.Series(0.00001, index=IDX), universe="point_in_time")
    gate = gate_with({"core_book": entry("core")})
    rep = g3.evaluate_book(gate, book="core_book", account=acc, sleeves={}, source=FINAL,
                           registry=reg({"core_book": 6}), first_run_at=datetime(2026, 11, 25, tzinfo=UTC),
                           as_of=datetime(2026, 12, 1, tzinfo=UTC), unlogged_configurations=6)
    assert rep["book_configurations"] == {"idea": "core", "books": ["core_book"], "configurations": 12}
    assert rep["marks"]["b2"]["numbers"]["chance"]["configurations"] == 12
    assert rep["gate_version"] == "3.1"
    text = g3.render_book(rep)
    assert "Chance test (gate v3.1)" in text and "N = 12 configurations of the idea `core`" in text


# ---------------------------------------------------------------- hindsight


def amended(log) -> Prereg:
    heads = tuple(h for h, _ in log)
    return Prereg("core_book", Path("docs/prereg/core_book.md"), "ab" * 32, "pre-registered", date(2026, 11, 20),
                  ("n1",), {}, datetime(2026, 11, 20, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC), (), heads,
                  tuple(log))


def test_an_amendment_committed_after_the_first_run_is_hindsight_in_the_verdict():
    p = amended([("## Amendment 1 (before)", datetime(2026, 11, 21, tzinfo=UTC)),
                 ("## Amendment 2 (after the 27% run)", datetime(2026, 11, 25, 17, tzinfo=UTC))])
    late = g3.hindsight(p, datetime(2026, 11, 24, tzinfo=UTC))
    assert [a["amendment"] for a in late] == ["## Amendment 2 (after the 27% run)"]
    assert g3.verdict_text("fail", late) == "fail, hindsight (Amendment 2 written after the first run)"
    assert g3.verdict_text("fail", []) == "fail"
    assert g3.hindsight(p, datetime(2026, 11, 26, tzinfo=UTC)) == []
    unknown = amended([("## Amendment 1 (no git)", None)])
    assert g3.hindsight(unknown, datetime(2026, 11, 26, tzinfo=UTC))[0]["committed_at"] is None


def test_the_first_logged_run_decides_not_the_time_of_the_run_being_judged():
    """A book script passes its own run time as `first_run_at`; an earlier logged run of the idea wins."""
    s = spy(12)
    acc = g3.Account(0.6 * s + 0.0003, s, costs=pd.Series(0.00001, index=IDX), universe="point_in_time")
    p = amended([("## Amendment 1 (a later cap)", datetime(2026, 11, 25, 17, tzinfo=UTC))])
    rep = g3.evaluate_book(gate_with({"core_book": entry()}), book="core_book", account=acc, sleeves={},
                           prereg=p, source=FINAL, registry=reg({"core_book": 6}),  # logged 24 Nov
                           first_run_at=datetime(2026, 11, 25, 18, tzinfo=UTC), as_of=datetime(2026, 12, 1, tzinfo=UTC))
    assert rep["verdict_text"].startswith(rep["verdict"] + ", hindsight (Amendment 1")
    assert "**Verdict: " + rep["verdict_text"] in g3.render_book(rep)


def test_index_core_amendment_4_is_hindsight_from_its_git_history():
    p = pr.load("index_core", ROOT)
    log = dict(p.amendment_log)
    head = next(h for h in p.amendments if h.startswith("## Amendment 4"))
    assert log[head] is not None and log[head].date() == date(2026, 11, 25)
    late = g3.hindsight(p, datetime(2026, 11, 24, 12, tzinfo=UTC))  # the first 27% run, #458
    assert [a["amendment"] for a in late] == [head]
    assert "hindsight (Amendment 4" in g3.verdict_text("fail", late)
    assert p.summary()["amendment_log"][-1]["amendment"] == head


def test_without_git_a_pre_registration_has_no_amendment_times(tmp_path):
    d = tmp_path / "docs" / "prereg"
    d.mkdir(parents=True)
    src = (ROOT / "docs" / "prereg" / "index_core.md").read_text()
    (d / "index_core.md").write_text(src)
    p = pr.load("index_core", tmp_path, committed=False)
    assert p.amendments and all(t is None for _, t in p.amendment_log)
    assert len(g3.hindsight(p, datetime(2026, 11, 24, tzinfo=UTC))) == len(p.amendments)
    assert replace(p, amendment_log=()).amendment_log == ()
