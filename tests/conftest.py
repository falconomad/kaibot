"""CI sharding (#221): `KAIBOT_TEST_SHARD=k/n` runs only the k-th of n disjoint parts of the collected tests.

Every test lands in exactly one part (a stable CRC32 of its node id, modulo n), so the n CI jobs together run the
same tests as one plain `pytest` run; nothing is skipped or marked. Without the variable nothing changes.
"""

from __future__ import annotations

import os

import pytest
from ci_shard import parse_shard, shard_of


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = os.environ.get("KAIBOT_TEST_SHARD")
    if not value:
        return
    k, n = parse_shard(value)
    keep = [it for it in items if shard_of(it.nodeid, n) == k]
    drop = [it for it in items if shard_of(it.nodeid, n) != k]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep


def pytest_report_header(config: pytest.Config) -> str | None:
    value = os.environ.get("KAIBOT_TEST_SHARD")
    return f"KAIBOT_TEST_SHARD={value}: one part of the suite; the other CI jobs run the rest" if value else None
