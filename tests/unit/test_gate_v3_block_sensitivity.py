"""M17 ticket 6.9 (#709): the pre-registered sensitivity of gate v3's bootstrap block length at 60 and 120
sessions. The addendum must be committed before any verdict uses it, must not change the judged gate, and every
results file that reports these block lengths must come after it in the history."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
import yaml

from kaibot.engine.config import ROOT

ADDENDUM = ROOT / "docs" / "prereg" / "addenda" / "gate_v3_bootstrap_blocks.md"
RESULTS = ROOT / "docs" / "research" / "results"
USES = re.compile(r"block-sensitive|mean block of (60|120) sessions|block (60|120)\b", re.I)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def test_the_addendum_freezes_60_and_120_and_keeps_the_judged_bootstrap_at_20():
    text = ADDENDUM.read_text()
    assert "**60 sessions** and **120 sessions**" in text and "seed" in text.lower()
    assert "never turns a fail into a" in text and "block-sensitive" in text and "#709" in text
    boot = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())["bootstrap"]
    assert boot == {"method": "stationary", "mean_block_sessions": 20, "runs": 2000, "seed": 16, "interval": 0.90}
    for n in ("2,000", "16", "90%", "2017-05-01 to 2024-12-31"):
        assert n in text, n


def test_the_addendum_is_not_a_rule_prereg():
    """It lives under docs/prereg/addenda/, so the checks that every docs/prereg/*.md is a rule or a book (and the
    trial guard's registration) never treat it as one."""
    assert ADDENDUM.parent.name == "addenda" and ADDENDUM.parent.parent == ROOT / "docs" / "prereg"
    assert ADDENDUM not in set((ROOT / "docs" / "prereg").glob("*.md"))


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_no_results_file_uses_the_sensitivity_before_the_addendum_was_committed():
    if _git("rev-parse", "--is-shallow-repository") == "true":
        pytest.skip("a shallow clone has no history to compare (CI checks out the full history)")
    if _git("status", "--porcelain", "--", str(ADDENDUM.relative_to(ROOT))):
        pytest.skip("the addendum is not committed yet")
    first = _git("log", "--diff-filter=A", "--format=%ct", "--", str(ADDENDUM.relative_to(ROOT))).splitlines()
    assert first, "the addendum is not in the history"
    t0 = int(first[-1])
    for f in sorted(RESULTS.rglob("*")):
        if not f.is_file() or f.suffix not in (".md", ".json") or not USES.search(f.read_text(errors="ignore")):
            continue
        # the first commit of this file that already used the sensitivity must come after the addendum
        log = _git("log", "--reverse", "--format=%H %ct", "--", str(f.relative_to(ROOT))).splitlines()
        for line in log:
            sha, ct = line.split()
            body = _git("show", f"{sha}:{f.relative_to(ROOT).as_posix()}")
            if USES.search(body):
                assert int(ct) >= t0, f"{f.name} used the block sensitivity before the addendum was committed"
                break
