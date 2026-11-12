"""Code fingerprint per rule plus a shared core (issue #126, audit G2): a change to rule B does not restart rule
A's evidence clock, and strategies/, broker/ and evaluation/ are covered."""

from __future__ import annotations

import glob
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import fingerprint as fpr
from kaibot.evaluation import gate_report as gr

SPEC = {"core": ["core/*.py", "strategies/*.py", "config.yaml"],
        "strategies": {"rule_a": ["strategies/rule_a.py"], "rule_b": ["strategies/rule_b.py", "replay/rule_b.py"]}}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for rel in ("core/engine.py", "strategies/__init__.py", "strategies/shared.py", "strategies/rule_a.py",
                "strategies/rule_b.py", "replay/rule_b.py", "config.yaml", "docs/readme.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(f"# {rel}\n")
    return tmp_path


def parts(root: Path) -> dict:
    return fpr.parse(fpr.fingerprint(root, SPEC))


def test_a_change_to_rule_b_does_not_touch_rule_a(tree: Path):
    before = parts(tree)
    (tree / "strategies" / "rule_b.py").write_text("THRESHOLD = 2\n")
    after = parts(tree)
    assert after["strategies"]["rule_a"] == before["strategies"]["rule_a"]  # rule A's clock keeps running
    assert after["strategies"]["rule_b"] != before["strategies"]["rule_b"]
    assert after["core"] == before["core"] and after["all"] != before["all"]
    (tree / "replay" / "rule_b.py").write_text("x = 1\n")  # a rule's file outside strategies/ is the rule's too
    assert parts(tree)["strategies"]["rule_a"] == before["strategies"]["rule_a"]


def test_a_change_to_the_core_restarts_every_rule(tree: Path):
    before = parts(tree)
    (tree / "core" / "engine.py").write_text("x = 2\n")
    after = parts(tree)
    assert after["core"] != before["core"]
    assert all(after["strategies"][k] != before["strategies"][k] for k in ("rule_a", "rule_b"))


def test_a_strategy_file_nobody_claims_is_shared(tree: Path):
    before = parts(tree)
    (tree / "strategies" / "shared.py").write_text("x = 2\n")
    assert parts(tree)["strategies"]["rule_a"] != before["strategies"]["rule_a"]
    again = parts(tree)
    (tree / "strategies" / "rule_c.py").write_text("new = True\n")  # a new rule file that no rule claims yet
    assert parts(tree)["core"] != again["core"]  # the safe side: everyone's clock restarts until it is claimed


def test_files_outside_the_spec_do_not_count(tree: Path):
    before = fpr.fingerprint(tree, SPEC)
    (tree / "docs" / "readme.md").write_text("changed\n")
    assert fpr.fingerprint(tree, SPEC) == before


def test_the_string_the_engine_logs(tree: Path):
    fp = fpr.fingerprint(tree, SPEC)
    assert re.fullmatch(r"[0-9a-f]{16};core=[0-9a-f]{16};rule_a=[0-9a-f]{16};rule_b=[0-9a-f]{16}", fp)
    p = fpr.parse(fp)
    assert fpr.compose(p) == fp and fp.startswith(p["all"])  # the dashboard shows the first 8 characters
    assert fpr.for_strategy(fp, "rule_a") == p["strategies"]["rule_a"]
    assert fpr.for_strategy(fp, "a_rule_without_files") == p["core"]
    assert fpr.for_strategy(fp, None) == fp
    assert fpr.fingerprint(tree, SPEC) == fp  # deterministic


def test_a_fingerprint_without_parts_is_its_own_fingerprint():
    assert fpr.parse("0123456789abcdef") is None and fpr.parse(None) is None and fpr.parse("a;b") is None
    assert fpr.for_strategy("0123456789abcdef", "rule_a") == "0123456789abcdef"  # written before per-rule parts
    assert fpr.for_strategy(None, "rule_a") is None


def test_a_plain_list_still_gives_the_old_hash(tree: Path):
    """The format before this change, computed the way gate_report.fingerprint used to."""
    patterns = ["core/*.py", "strategies/*.py"]
    h = hashlib.sha256()
    for f in sorted({p for pat in patterns for p in glob.glob(str(tree / pat))}):
        h.update(Path(f).relative_to(tree).as_posix().encode() + b"\0" + Path(f).read_bytes() + b"\0")
    assert fpr.fingerprint(tree, patterns) == h.hexdigest()[:16] == gr.fingerprint(tree, patterns)


def test_rule_names_that_would_break_the_string_are_refused(tree: Path):
    for name in ("core", "a;b", "a=b"):
        with pytest.raises(ValueError):
            fpr.fingerprint(tree, {"core": ["core/*.py"], "strategies": {name: ["strategies/rule_a.py"]}})


# ---------------------------------------------------------------- the real spec (config/gate.yaml)


def real_spec() -> dict:
    return yaml.safe_load((ROOT / "config" / "gate.yaml").read_text())["fingerprint"]


def test_strategies_broker_and_evaluation_are_covered():
    """Audit G2: SPY momentum's thresholds, the cost model and the gate's own arithmetic could change without
    restarting the clock. Every Python file of the trading and evaluation code is now in a fingerprint."""
    spec = real_spec()
    folders = [f"src/kaibot/{d}" for d in ("strategies", "broker", "evaluation", "engine", "risk", "replay",
                                           "features")]
    assert fpr.uncovered(ROOT, spec, folders) == []
    files = fpr.fileset(ROOT, spec)
    for rel in ("src/kaibot/broker/shadow_costs.py", "src/kaibot/evaluation/gate_report.py",
                "src/kaibot/evaluation/metrics.py", "src/kaibot/strategies/shadow_types.py"):
        assert rel in files["core"]
    assert "src/kaibot/strategies/spy_momentum.py" in files["strategies"]["spy_late_momentum"]
    assert "src/kaibot/strategies/spy_momentum.py" not in files["core"]  # a rule's own file is not core


def test_every_rule_the_engine_runs_has_its_own_part():
    spec = real_spec()
    prof = yaml.safe_load((ROOT / "config" / "profiles.yaml").read_text())
    live = {s for lv in prof["levels"].values() for s in lv["strategies"]} | set(prof["shadow_strategies"])
    assert live <= set(spec["strategies"])
    fp = gr.fingerprint(ROOT, spec)
    got = fpr.parse(fp)
    assert set(got["strategies"]) == set(spec["strategies"]) and re.fullmatch(r"[0-9a-f]{16}", fp[:16])
    assert len({got["core"], *got["strategies"].values()}) == 1 + len(got["strategies"])  # all different
