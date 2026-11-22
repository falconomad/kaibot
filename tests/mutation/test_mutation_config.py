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


def test_an_unreadable_results_file_falls_back_to_the_last_whole_copy(tmp_path):
    """#561: the stop at the end of the budget landed while mutmut rewrote its results file (it was empty)."""
    meta = tmp_path / "mutants" / "src" / "kaibot" / "engine" / "lease.py.meta"
    meta.parent.mkdir(parents=True)
    meta.with_name(meta.name + ".good").write_text('{"exit_code_by_key": {"m_1": 1, "m_2": 0, "m_3": null}}')
    meta.write_text("")
    got = mr.results(tmp_path, "src/kaibot/engine/lease.py")
    assert got == {"m_1": "killed", "m_2": "survived", "m_3": "not checked"}


def test_the_budget_stop_keeps_the_results_a_torn_write_would_lose(tmp_path, monkeypatch):
    """A stand-in for mutmut: writes its results, then on the interrupt empties the file (the torn write) and exits."""
    fake = tmp_path / "fake_mutmut.py"
    fake.write_text(
        "import json, signal, sys, time\n"
        "from pathlib import Path\n"
        "meta = Path('mutants/x.py.meta'); meta.parent.mkdir(parents=True, exist_ok=True)\n"
        "meta.write_text(json.dumps({'exit_code_by_key': {'a': 1, 'b': 0}}))\n"
        "def torn(*_):\n"
        "    meta.write_text(''); sys.exit(1)\n"
        "signal.signal(signal.SIGINT, torn)\n"
        "time.sleep(60)\n")
    monkeypatch.setattr(mr, "mutmut_bin", lambda: [sys.executable, str(fake)])
    monkeypatch.setattr(mr, "SNAPSHOT_S", 0.2)
    work = tmp_path / "work"
    work.mkdir()
    with open(tmp_path / "log", "w") as log:
        complete, took = mr.run_mutmut(work, 1.5 / 60, 1, log, work / "mutants" / "x.py.meta")
    assert not complete and took < 20
    assert (work / "mutants" / "x.py.meta").read_text() == ""  # the stop tore the file, as on the runner
    assert mr.results(work, "x.py") == {"a": "killed", "b": "survived"}
