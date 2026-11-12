"""One trial registry (issue #126, audit F5): configurations are counted, not rows; the walk-forward scripts, the
trial report and the gate reports all get the same number; the registry is found inside the engine container."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_report as gr
from kaibot.evaluation import trials

sys.path.insert(0, str(ROOT / "scripts"))
import trial_report  # noqa: E402
import walk_forward  # noqa: E402

SAMPLE = ROOT / "tests" / "fixtures" / "trials_sample.jsonl"
PINNED = 4159  # 3105 + 1035 + 12 searched by three runs, seven runs of one configuration, one cut-off line


class Store:
    """PostgREST as the registry uses it: `limit` and `offset` honoured, a missing table raises."""

    def __init__(self, tables: dict[str, list[dict]], honour_offset: bool = True) -> None:
        self.tables, self.honour_offset, self.calls = tables, honour_offset, []

    def select(self, table, params):
        self.calls.append(table)
        if table not in self.tables:
            raise RuntimeError(f"GET {table} -> 404")
        rows = [dict(r, id=i + 1) for i, r in enumerate(self.tables[table])]
        a = int(params.get("offset", 0)) if self.honour_offset else 0
        return rows[a:a + int(params.get("limit", 1000))]  # PostgREST caps a request at 1,000 rows

    def insert(self, table, rows, returning=True):
        self.tables.setdefault(table, []).extend(rows if isinstance(rows, list) else [rows])


# ---------------------------------------------------------------- the count


def test_sample_log_counts_configurations_not_rows():
    rows, unreadable = trials.read_jsonl(SAMPLE)
    assert (len(rows), unreadable) == (10, 1)  # what counting rows would have said: 10
    assert trials.count_file(SAMPLE) == PINNED
    reg = trials.load(None, SAMPLE)
    assert reg.configurations == PINNED and len(reg.rows) == 9  # the run that was logged twice is one trial
    assert reg.by_strategy() == {"dip_reversal": 3120, "orb_stocks_in_play": 2, "spy_late_momentum": 1,
                                 "trend_highs": 1035}
    assert sum(reg.by_strategy().values()) + reg.unreadable == PINNED


def test_every_consumer_gets_the_same_count(tmp_path: Path):
    """Audit F5: the walk-forward summed `n_trials`, the gate counted rows. Now one function serves all four."""
    assert walk_forward.logged_trials(SAMPLE) == PINNED
    (tmp_path / "trials.jsonl").write_text(SAMPLE.read_text())
    assert trial_report.report(tmp_path)["n_trials_logged"] == PINNED
    store = Store({"trade": [], "daily_result": [], "gate_random": [], "event": [], "trial": []})
    rep = gr.build(store, gr.Bar.load(ROOT), 1, "fp", {"amount_usd": 5000}, 12.0,
                   ["dip_reversal", "spy_late_momentum"], SAMPLE)
    assert rep["trial_registry"]["configurations"] == PINNED
    assert {s["n_trials"] for s in rep["strategies"].values()} == {PINNED}  # all rules, one registry
    assert rep["strategies"]["dip_reversal"]["n_trial_sharpes"] == 4  # the Sharpes behind V are per rule


def test_what_one_row_stands_for():
    n = trials.configurations_of
    assert n({"n_trials": 3105}) == 3105 and n({}) == 1 and n({"n_trials": None}) == 1
    assert n({"n_trials": "27"}) == 27 and n({"n_trials": 27.0}) == 27
    assert n({"n_trials": -4}) == 1 and n({"n_trials": "x"}) == 1  # unreadable: never less than the run itself
    assert n({"n_trials": 0}) == 0  # a report of a configuration another row counts (2x costs, stress fill)
    assert n({"n_trials": True}) == 1 and n({"n_trials": float("nan")}) == 1 and n({"n_trials": "many"}) == 1


def test_missing_file_counts_nothing_and_a_duplicated_id_keeps_the_larger_search(tmp_path: Path):
    f = tmp_path / "trials.jsonl"
    assert trials.count_file(f) == 0 and trials.count_file(None) == 0
    f.write_text('{"trial_id": "a", "n_trials": 3}\n{"trial_id": "a", "n_trials": 9}\n{"trial_id": "b"}\n[1, 2]\n')
    assert trials.count_file(f) == 9 + 1 + 1  # "a" once at its larger size, "b", and the line that is no object


def test_daily_sharpes_feed_the_variance():
    out = trials.load(None, SAMPLE).daily_sharpes()
    assert out["trend_highs"] == [pytest.approx(0.55 / 252**0.5)]  # logged twice, used once
    assert len(out["dip_reversal"]) == 4 and "?" not in out


# ---------------------------------------------------------------- file plus database


def test_a_run_in_the_file_and_in_the_database_counts_once():
    store = Store({
        "trial": [  # migration 0001: config replays, no trial id
            {"strategy": "dip_reversal", "params": {"kind": "config", "run_id": 7, "config_version": 3},
             "metrics": {"daily_sharpe_ann": 0.2}},  # also in the file as config_r7_v3
            {"strategy": "dip_reversal", "params": {"kind": "config", "run_id": 8, "config_version": 1},
             "metrics": {"daily_sharpe_ann": 0.1}}],  # only here: +1
        "trial_registry": [  # migration 0015
            {"trial_id": "balanced_wf_A_20261108T100000_tuned", "strategy": "dip_reversal", "n_trials": 3105,
             "row": {"metrics": {"daily_sharpe_ann": -2.51}}},  # also in the file
            {"trial_id": "balanced_md_rel_strength_x_tuned", "strategy": "rel_strength", "n_trials": 690,
             "row": {"metrics": {"daily_sharpe_ann": 0.3}}}]})  # only here: +690
    reg = trials.load(store, SAMPLE)
    assert reg.configurations == PINNED + 1 + 690
    assert reg.by_strategy()["rel_strength"] == 690 and reg.by_strategy()["dip_reversal"] == 3121
    assert len(reg.daily_sharpes()["dip_reversal"]) == 5
    assert any("trial_registry: 2 rows" in s for s in reg.sources) and any("trial: 2 rows" in s for s in reg.sources)


def test_before_the_migration_the_registry_table_is_simply_absent():
    store = Store({"trial": [{"strategy": "dip_reversal", "params": {"kind": "config", "run_id": 8,
                                                                    "config_version": 1}, "metrics": {}}]})
    reg = trials.load(store, SAMPLE)
    assert reg.configurations == PINNED + 1 and store.calls == ["trial", "trial_registry"]
    assert any("not readable" in s for s in reg.sources)


def test_an_unreachable_database_is_asked_once_and_the_file_still_counts():
    store = Store({})  # every table raises: the database cannot be reached
    reg = trials.load(store, SAMPLE)
    assert reg.configurations == PINNED and store.calls == ["trial"]  # no second slow attempt at day end


def test_tables_longer_than_one_page_are_read_whole():
    rows = [{"trial_id": f"t{i}", "strategy": "x", "n_trials": 2, "row": {}} for i in range(2300)]
    assert trials.load(Store({"trial": [], "trial_registry": rows}), None).configurations == 4600
    stuck = Store({"trial": [], "trial_registry": rows}, honour_offset=False)  # a store that ignores `offset`
    assert trials.load(stuck, None).configurations == 2000 and len(stuck.calls) < 10  # ends, does not loop


# ---------------------------------------------------------------- where the file is (the container)


def test_registry_file_is_found_inside_the_container(tmp_path: Path):
    root, data = tmp_path / "app", tmp_path / "data"  # the image has no /app/data; /data is the mounted volume
    root.mkdir()
    data.mkdir()
    default = root / "data" / "trials.jsonl"
    assert trials.registry_file(root, {}) == default  # nothing anywhere: the default path, reported as not found
    (data / "trials.jsonl").write_text("{}\n")
    assert trials.registry_file(root, {"KAIBOT_DATA_DIR": str(data)}) == data / "trials.jsonl"
    default.parent.mkdir()
    default.write_text("{}\n")
    assert trials.registry_file(root, {"KAIBOT_DATA_DIR": str(data)}) == default  # the repo's own log wins
    assert trials.registry_file(root, {"KAIBOT_TRIALS_FILE": "/registry/trials.jsonl",
                                       "KAIBOT_DATA_DIR": str(data)}) == Path("/registry/trials.jsonl")


def test_day_end_hook_reads_the_registry_from_the_data_volume(tmp_path: Path, monkeypatch):
    """The path the engine's day-end hook hands to the report: with the image's layout it is the mounted one."""
    (tmp_path / "trials.jsonl").write_text(SAMPLE.read_text())
    monkeypatch.setenv("KAIBOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("KAIBOT_TRIALS_FILE", raising=False)
    found = trials.registry_file(tmp_path / "app")
    assert found == tmp_path / "trials.jsonl" and trials.count_file(found) == PINNED


# ---------------------------------------------------------------- writing


def test_append_only_adds_a_line(tmp_path: Path):
    f = tmp_path / "sub" / "trials.jsonl"
    row = trials.append(f, {"trial_id": "a", "strategy": "trend_highs", "n_trials": 1035, "prereg_sha256": "ab"})
    assert row["n_trials"] == 1035 and json.loads(f.read_text()) == row
    f.write_text(f.read_text() + '{"trial_id": "cut", "strategy": "x"')  # a crashed run left half a line
    before = f.read_bytes()
    trials.append(f, {"strategy": "rel_strength"})
    assert f.read_bytes().startswith(before)  # nothing that was there is rewritten
    rows, bad = trials.read_jsonl(f)
    assert [r["strategy"] for r in rows] == ["trend_highs", "rel_strength"] and bad == 1  # not glued together
    assert rows[1]["n_trials"] == 1 and trials.count_file(f) == 1035 + 1 + 1
    for wrong in ({"n_trials": 3}, {"strategy": ""}, {"strategy": "x", "n_trials": -1},
                  {"strategy": "x", "n_trials": 2.5}, {"strategy": "x", "n_trials": True}):
        with pytest.raises(ValueError):
            trials.append(f, wrong)


def test_push_copies_only_what_the_database_lacks():
    rows, _ = trials.read_jsonl(SAMPLE)
    store = Store({"trial": [], "trial_registry": []})
    assert trials.push(store, rows) == 9  # ten lines, nine trials
    assert trials.push(store, rows) == 0  # nothing is sent twice: the table refuses updates anyway
    table = store.tables["trial_registry"]
    assert {r["trial_id"] for r in table} >= {"config_r7_v3", "balanced_wf_A_20261108T100000_tuned"}
    assert sum(r["trial_id"].startswith("sha256:") for r in table) == 2  # the two shadow runs without an id
    assert all(r["n_trials"] >= 1 and r["strategy"] and r["row"] for r in table)
    assert trials.load(store, SAMPLE).configurations == PINNED  # file and copy together: still one registry
    assert trials.load(store, None).configurations == PINNED - 1  # the copy alone lacks only the cut-off line


def test_rows_are_made_safe_for_the_database():
    row = trials.db_row({"trial_id": "a", "ts": "old", "strategy": None, "n_trials": "27",
                         "metrics": {"daily_sharpe_ann": float("nan"), "best": [float("inf"), 1.5]}})
    assert row == {"trial_id": "a", "ts": None, "strategy": "?", "n_trials": 27,
                   "row": {"trial_id": "a", "ts": "old", "strategy": None, "n_trials": "27",
                           "metrics": {"daily_sharpe_ann": None, "best": [None, 1.5]}}}
    json.dumps(row, allow_nan=False)  # Postgres takes it
    assert trials.db_row({"trial_id": "b", "ts": "2026-11-08T10:00:00Z", "strategy": "x"})["ts"] == \
        "2026-11-08T10:00:00+00:00"


def test_migration_0015_is_append_only_and_not_shared():
    files = list((ROOT / "migrations").glob("0015_*"))
    assert [f.name for f in files] == ["0015_m7_trial_registry.sql"]  # the number is not shared
    sql = files[0].read_text()
    assert "create table if not exists kaibot.trial_registry" in sql and "NOT APPLIED" in sql
    assert "trial_id   text        not null unique" in sql and "check (n_trials >= 0)" in sql
    assert "before update or delete on kaibot.trial_registry" in sql
    assert "before truncate on kaibot.trial_registry" in sql
    assert "revoke all on kaibot.trial_registry from public, anon, authenticated, service_role" in sql
    assert "grant select, insert on kaibot.trial_registry to service_role" in sql
    assert "enable row level security" in sql and "drop table" not in sql.lower()
    for col in ("trial_id", "ts", "strategy", "n_trials", "row"):  # what trials.db_row sends
        assert col in trials.db_row({"trial_id": "a", "strategy": "x"})
        assert re.search(rf'^\s+"?{col}"?\s', sql, re.M), col
    assert trials.TABLE == "trial_registry"


def test_momentum_core_reports_count_as_configurations_not_rows():
    """scripts/momentum_core.py (#127) logs every grid run; only the base fill at 1x costs is a configuration."""
    rows = [{"trial_id": f"core_{fill}_{cost}x_cap{cap}", "strategy": "trend_momentum",
             "n_trials": int(fill == "base" and cost == 1.0)}
            for fill in ("base", "stress") for cost in (0.0, 1.0, 2.0, 3.0) for cap in (30, 60, 100)]
    assert len(rows) == 24 and trials.count_configurations(rows) == 3  # one per exposure cap
