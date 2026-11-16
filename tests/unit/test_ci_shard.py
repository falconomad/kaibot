"""The CI shards (#221) split the suite into disjoint parts that together cover every test."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from ci_shard import parse_shard, shard_of

ROOT = Path(__file__).resolve().parents[2]


def test_every_test_lands_in_exactly_one_part():
    ids = [f"tests/unit/test_x.py::test_{i}[case-{i % 7}]" for i in range(2000)]
    for n in (1, 2, 3):
        parts = [{x for x in ids if shard_of(x, n) == k} for k in range(1, n + 1)]
        assert sum(len(p) for p in parts) == len(ids) and set().union(*parts) == set(ids)
        assert all(len(p) > len(ids) / n * 0.9 for p in parts)  # roughly even


def test_the_part_does_not_depend_on_the_process():
    code = "from ci_shard import shard_of; print(shard_of('tests/test_engine.py::test_a', 2))"
    runs = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT / "tests")}, check=True).stdout
            for seed in ("0", "1", "random")}
    assert runs == {f"{shard_of('tests/test_engine.py::test_a', 2)}\n"}


@pytest.mark.parametrize("bad", ["0/2", "3/2", "1/0", "x/2", "2"])
def test_a_wrong_shard_is_refused(bad):
    with pytest.raises(ValueError):
        parse_shard(bad)


def test_the_two_ci_parts_collect_the_whole_suite_between_them():
    def collected(shard: str | None) -> set[str]:
        env = {k: v for k, v in os.environ.items() if k != "KAIBOT_TEST_SHARD"}
        if shard:
            env["KAIBOT_TEST_SHARD"] = shard
        out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                              "tests/unit/test_ci_shard.py", "tests/unit/test_gate.py"],
                             cwd=ROOT, capture_output=True, text=True, env=env, check=True).stdout
        return {x for x in out.splitlines() if "::" in x}

    whole, one, two = collected(None), collected("1/2"), collected("2/2")
    assert whole and one and two and not one & two and one | two == whole
