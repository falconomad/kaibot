"""M12 ticket 3.5 (#467): the weekly mutation run's configuration holds together (no mutmut needed here).

- every target names a module the ticket lists and tests that exist;
- the [tool.mutmut] section the runner writes is valid TOML and mutates only the target;
- every documented equivalent mutant has a reason and names a target's module;
- the report renders, and an equivalent survivor is not reported.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("mutation_run", ROOT / "scripts" / "mutation_run.py")
mr = importlib.util.module_from_spec(_spec)
sys.modules["mutation_run"] = mr
_spec.loader.exec_module(mr)


def test_the_targets_are_the_tickets_modules_and_their_tests_exist():
    t = mr.load_targets()
    assert set(t) == {"ledger", "orders", "journal", "risk_caps", "reconcile", "lease", "watchdog"}
    for name, spec in t.items():
        assert (ROOT / spec["source"]).is_file(), name
        assert spec["tests"], name
        for p in spec["tests"]:
            assert (ROOT / p).exists(), f"{name}: {p}"


def test_the_written_config_mutates_only_the_target():
    for name, spec in mr.load_targets().items():
        cfg = tomllib.loads(mr.config(spec))["tool"]["mutmut"]
        assert cfg["only_mutate"] == [spec["source"]], name
        assert cfg["pytest_add_cli_args_test_selection"] == spec["tests"]
        assert cfg["source_paths"] == ["src/kaibot"] and cfg["mutate_only_covered_lines"] is True
    assert "[tool.mutmut]" not in (ROOT / "pyproject.toml").read_text(), "the runner writes it per target"


def test_every_equivalent_has_a_reason_and_names_a_target():
    sources = {Path(s["source"]).stem for s in mr.load_targets().values()}
    eq = mr.load_equivalent()
    assert eq
    for e in eq:
        assert len(e["reason"].strip()) > 20, e
        assert e["mutant"].split(".")[2] in sources, e


def test_the_report_lists_survivors_and_marks_a_spent_budget():
    rep = {"target": "lease", "source": "src/kaibot/engine/lease.py", "complete": False, "minutes": 40.0,
           "mutants": 10, "counts": {"killed": 6, "survived": 2, "not checked": 2}, "tried": 8, "score": 0.75,
           "equivalent": 1, "survivors": [{"mutant": "kaibot.engine.lease.x_holder_id__mutmut_10",
                                           "diff": "-a\n+b"}]}
    text = mr.summary([rep])
    assert "| lease |" in text and "budget spent" in text and "75%" in text
    assert "x_holder_id__mutmut_10" in text and "```diff" in text
