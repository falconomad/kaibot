"""M10 ticket 1.22 (#447): one registry of event names (engine/event_names.py); the dashboard's lists are generated
from it.

- Every name the code emits is registered: literal names by a scan of src/ and scripts/, dynamic ones through the
  known sources of tests/test_kb_health_engine_events_sql.py (a new dynamic call fails there until it is added).
- Every event any test writes through a store is registered: the stores call `event_names.note`, which
  tests/conftest.py makes strict, so the whole suite checks the names that really reach `kaibot.event`.
- The generated lists are not stale: the latest kb_health migration lists exactly HEALTH, in order, and
  apps/web/src/lib/eventNames.ts is the generator's output (`python -m kaibot.engine.event_names --ts`).
- At run time an unregistered name is written anyway and logged once: the registry never costs an event.
"""

from __future__ import annotations

import logging

import pytest
from test_kb_health_engine_events_sql import _emitted, _latest_kb_health
from test_kb_health_news_events_sql import _sql, _types

from kaibot.engine import event_names as en
from kaibot.engine.config import ROOT

TS = ROOT / "apps" / "web" / "src" / "lib" / "eventNames.ts"


def test_every_name_the_code_emits_is_registered():
    missing = _emitted() - en.NAMES
    assert not missing, f"add to engine/event_names.py (HEALTH or ROUTINE): {sorted(missing)}"


def test_every_registered_name_is_still_emitted():
    """A name nobody sends any more is taken out (or the scan lost it: then fix the scan)."""
    assert en.NAMES - _emitted() == set(), sorted(en.NAMES - _emitted())


def test_the_latest_kb_health_migration_lists_exactly_the_health_events():
    listed = _types(_sql(_latest_kb_health()))
    assert listed == list(en.HEALTH_NAMES), (
        "kb_health is stale: write the next kb_health migration with "
        "`PYTHONPATH=src python -m kaibot.engine.event_names --sql` as its type list "
        f"(missing {sorted(set(en.HEALTH_NAMES) - set(listed))}, extra {sorted(set(listed) - set(en.HEALTH_NAMES))})")


def test_the_generated_sql_list_parses_back_to_the_registry():
    sql = f"select 1 where type in (\n{en.health_sql()})\n        order by ts desc limit 80"
    assert _types(sql) == [*en.HEALTH_NAMES, *(e.name for e in en.PENDING)]


def test_a_pending_health_event_is_not_listed_yet():
    """Once the kb_health migration that lists it lands, it moves to the end of HEALTH (the test above says so)."""
    assert not {e.name for e in en.PENDING} & set(_types(_sql(_latest_kb_health())))


def test_the_dashboard_labels_are_generated_and_not_stale():
    assert TS.read_text() == en.labels_ts(), (
        "apps/web/src/lib/eventNames.ts is stale: run "
        "`PYTHONPATH=src python -m kaibot.engine.event_names --ts > apps/web/src/lib/eventNames.ts`")


def test_the_health_page_reads_the_generated_labels():
    page = (ROOT / "apps" / "web" / "src" / "pages" / "Health.tsx").read_text()
    assert 'import { EVENT_LABELS } from "../lib/eventNames";' in page
    assert "const EV: Record<string, string> = EVENT_LABELS;" in page
    assert "engine_start: \"Engine started\"" not in page  # the hand-kept map is gone


def test_no_name_is_both_health_and_routine_and_every_routine_one_says_why():
    assert not set(en.HEALTH_NAMES) & {e.name for e in (*en.PENDING, *en.ROUTINE)}
    assert all(e.why for e in en.ROUTINE) and all(not e.why for e in (*en.HEALTH, *en.PENDING))
    assert len(en.HEALTH_NAMES) == len(set(en.HEALTH_NAMES))


def test_a_name_registered_twice_is_an_error(monkeypatch):
    monkeypatch.setattr(en, "ROUTINE", (*en.ROUTINE, en.Event("halt", why="x")))
    with pytest.raises(ValueError, match="twice"):
        en._check_registry()


def test_strict_in_the_tests_an_unregistered_name_fails_the_write():
    from fakes import FakeStore

    assert en.STRICT
    with pytest.raises(en.UnregisteredEvent, match="not_a_real_event"):
        FakeStore().event("not_a_real_event", {})


def test_at_run_time_an_unregistered_name_is_written_and_logged_once(monkeypatch, caplog, tmp_path):
    from fakes import FakeStore

    from kaibot.engine.journal import Journal, JournalStore

    monkeypatch.setattr(en, "STRICT", False)
    monkeypatch.setattr(en, "_said", set())
    store = JournalStore(Journal(tmp_path / "j.sqlite"), FakeStore(), live=True)
    before = store.journal.last_seq()
    with caplog.at_level(logging.WARNING, logger="kaibot.engine"):
        store.event("brand_new_event", {"a": 1})
        store.event("brand_new_event", {"a": 2})
    assert caplog.text.count("not registered") == 1
    assert store.journal.last_seq() >= before + 2  # both written


def test_the_registered_store_paths_call_note():
    for f in ("src/kaibot/engine/store.py", "src/kaibot/engine/journal.py", "tests/fakes.py"):
        assert "event_names.note(kind)" in (ROOT / f).read_text(), f
