"""CI sharding helpers (#221); the hook that uses them is in tests/conftest.py."""

from __future__ import annotations

import zlib


def parse_shard(value: str) -> tuple[int, int]:
    k, n = (int(x) for x in value.split("/"))
    if not 1 <= k <= n:
        raise ValueError(f"KAIBOT_TEST_SHARD must be k/n with 1 <= k <= n, got {value!r}")
    return k, n


def shard_of(nodeid: str, n: int) -> int:
    """The part (1..n) a test belongs to; the same on every machine and every Python process."""
    return zlib.crc32(nodeid.encode()) % n + 1
