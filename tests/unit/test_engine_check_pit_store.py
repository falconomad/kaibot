"""#812 (hand-off from #817): the engine's `--check` reports the pit store check as a `pit_store` line, after
`daily_path`. A problem is a WARNING line, never FAILED, so `--check`'s exit code is unchanged."""

from __future__ import annotations

import inspect

from kaibot.engine import __main__ as entry
from kaibot.ingest import pit_store_check as psc


def test_pit_store_state_reads_the_store_the_daily_path_reads(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("KAIBOT_DAILY_DATA", str(tmp_path))
    monkeypatch.setattr(psc, "engine_warning", lambda root: seen.setdefault("root", root) and {"verdict": "OK"})
    assert entry.pit_store_state() == {"verdict": "OK"}
    assert str(seen["root"]) == str(tmp_path)


def test_pit_store_state_defaults_to_the_repo_data_folder(monkeypatch):
    seen = {}
    monkeypatch.delenv("KAIBOT_DAILY_DATA", raising=False)
    monkeypatch.setattr(psc, "engine_warning", lambda root: seen.setdefault("root", root))
    entry.pit_store_state()
    assert seen["root"] == entry.ROOT / "data"


def test_no_store_is_a_warning_never_a_failed_line(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIBOT_DAILY_DATA", str(tmp_path))  # no daily/ under it
    got = entry.pit_store_state()
    assert isinstance(got, str) and got.startswith("WARNING: ") and "FAILED" not in got
    assert not any(tmp_path.iterdir())  # read-only: nothing written


def test_check_block_runs_pit_store_right_after_daily_path():
    src = inspect.getsource(entry.main)
    daily = src.index('step("daily_path"')
    pit = src.index('step("pit_store", pit_store_state)')
    assert daily < pit and src[daily:pit].count("step(") == 1
