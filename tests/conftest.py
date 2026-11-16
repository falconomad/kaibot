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


# ---------------------------------------------------------------- #256: the SQL tests' scratch Postgres, skip budget

_SCRATCH = None


def pytest_configure(config: pytest.Config) -> None:
    """KAIBOT_TEST_PG_AUTO=1 (CI): the SQL tests that need a scratch Postgres with the migrations applied
    (KAIBOT_TEST_PG) get one: a private local cluster with every migration (tests/pg_scratch.py), one database per
    xdist worker. Never Supabase. Without Postgres binaries, or with KAIBOT_TEST_PG set already, nothing changes."""
    global _SCRATCH
    if os.environ.get("KAIBOT_TEST_PG_AUTO") != "1":
        return
    worker = getattr(config, "workerinput", None)
    if worker is not None:  # an xdist worker: its own copy of the template on the main process's cluster
        base = os.environ.get("KAIBOT_TEST_PG_BASE")
        if base and not os.environ.get("KAIBOT_TEST_PG"):
            from pg_scratch import attach

            os.environ["KAIBOT_TEST_PG"] = attach(base, f"kb_{worker['workerid']}")
        return
    if os.environ.get("KAIBOT_TEST_PG"):
        return
    from pg_scratch import ScratchPg

    try:
        _SCRATCH = ScratchPg()
    except (FileNotFoundError, OSError):
        return  # no binaries: the SQL tests stay skipped (and are counted)
    _SCRATCH.build_template()
    base = _SCRATCH.conn("postgres").rsplit(" -d ", 1)[0]
    os.environ["KAIBOT_TEST_PG_BASE"] = base  # the workers start after this and inherit it
    if not config.getoption("numprocesses", default=None):  # no xdist: this process runs the tests itself
        os.environ["KAIBOT_TEST_PG"] = _SCRATCH.copy("kb_main")


def pytest_unconfigure(config: pytest.Config) -> None:
    if _SCRATCH is not None and getattr(config, "workerinput", None) is None:
        _SCRATCH.stop()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """KAIBOT_MAX_SKIPPED (CI): more skipped tests than docs/ops/skipped_tests.md accounts for fails the run, so a
    new skip has to be written down there (and its reason is printed by `-rs`)."""
    limit = os.environ.get("KAIBOT_MAX_SKIPPED")
    if not limit or getattr(session.config, "workerinput", None) is not None:
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    n = len(tr.stats.get("skipped", [])) if tr is not None else 0
    if n > int(limit):
        if tr is not None:
            tr.write_line(f"{n} tests skipped, more than the {limit} docs/ops/skipped_tests.md accounts for: "
                          "write the new skip down there (or make it run)", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
