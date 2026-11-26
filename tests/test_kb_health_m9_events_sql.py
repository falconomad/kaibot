"""Migration 0039 (kb_health's event list): the health events of M9 tickets 1.12, 1.10 and 1.17 reach the dashboard
through kb_health's `events`: `replica_poison` and `replica_backlog` (#437), `clock_skew` (#435) and
`calendar_expiring` (#442).

The file checks run everywhere: 0039's function is 0036's, byte for byte, with only the new types added, and it keeps
0021's revoke/grant. tests/test_kb_health_engine_events_sql.py checks that the engine still sends every listed type.
The SQL checks need a scratch Postgres with every migration applied (KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1
build one; skipped otherwise)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_kb_health_engine_events_sql import _emitted
from test_kb_health_news_events_sql import OTHER, PG, _sql, _types, owner, psql  # noqa: F401 (owner: autouse)

ROOT = Path(__file__).resolve().parents[1]
M36 = ROOT / "migrations" / "0036_kb_health_dynamic_events.sql"
M39 = ROOT / "migrations" / "0039_kb_health_m9_events.sql"
NEW = ("replica_poison", "replica_backlog", "clock_skew", "calendar_expiring",
       "command_waiting", "command_rejected", "multi_day_book_unchecked", "multi_day_book_unconfirmed",
       "multi_day_unbooked_aged")  # last 5: #512
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0039 applied")


def test_0039_is_0036_with_only_the_new_types_added():
    old, new = _sql(M36), _sql(M39)
    assert _types(new) == _types(old) + list(NEW)
    head_old, tail_old = old.split("where type in (", 1)[0], old.split("order by ts desc limit 80", 1)[1]
    head_new, tail_new = new.split("where type in (", 1)[0], new.split("order by ts desc limit 80", 1)[1]
    assert (head_new, tail_new) == (head_old, tail_old)
    assert "revoke all on function public.kb_health() from public, anon;" in tail_new
    assert "grant execute on function public.kb_health() to authenticated;" in tail_new


def test_0039_is_the_latest_definition_of_kb_health_before_0055():
    """0055 (#693) is the next kb_health: 0039's text with more types (tests/test_kb_health_0055_*)."""
    later = sorted(p.name for p in (ROOT / "migrations").glob("*.sql")
                   if p.name > M39.name and "function public.kb_health()" in p.read_text())
    assert later[:1] == ["0055_kb_health_unregistered_event_alert.sql"]


def test_0039_has_no_destructive_statement():
    s = _sql(M39).lower()
    assert not re.search(r"\b(delete|drop|truncate|alter table|insert|update)\b", s)


def test_the_engine_sends_every_type_0039_adds():
    assert set(NEW) <= _emitted(), sorted(set(NEW) - _emitted())


def test_the_header_claims_no_status():
    assert not re.search(r"^--\s*(NOT APPLIED|APPLIED)\b", M39.read_text(), re.M)


INSERT = """insert into kaibot.event (ts, type, payload, hash) values
  (now() - interval '5 minutes', 'replica_poison', '{"seq": 7, "table": "halt"}', 't0039-a'),
  (now() - interval '4 minutes', 'replica_backlog', '{"pending": 40}', 't0039-b'),
  (now() - interval '3 minutes', 'clock_skew', '{"state": "skewed", "seconds": -240}', 't0039-c'),
  (now() - interval '2 minutes', 'calendar_expiring', '{"calendar": "macro_calendar"}', 't0039-d'),
  (now() - interval '1 minute', 'multi_day_column_missing', '{"seq": 9}', 't0039-e'),
  (now(), 'multi_day_fill', '{"symbol": "AAPL"}', 't0039-f');"""
SHOWN = ("replica_poison", "replica_backlog", "clock_skew", "calendar_expiring", "multi_day_column_missing",
         "multi_day_fill")


@needs_pg
def test_kb_health_returns_the_new_events_and_0036s_and_still_not_the_routine_ones():
    out = psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;").stdout.strip().splitlines()
    got = [e for e in json.loads(out[-1])["events"] if e["payload"] and e["type"] in SHOWN]
    assert [e["type"] for e in got] == ["multi_day_column_missing", "calendar_expiring", "clock_skew",
                                        "replica_backlog", "replica_poison"]


@needs_pg
def test_kb_health_stays_owner_only():
    r = psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;", OTHER, check=False)
    assert r.returncode and "not authorised" in r.stderr


@needs_pg
def test_the_read_only_check_of_migrations_pending_passes():
    out = psql("""select prosrc like '%replica_poison%' and prosrc like '%clock_skew%'
                     and prosrc like '%calendar_expiring%' and prosrc like '%multi_day_column_missing%'
                     from pg_proc where proname = 'kb_health';""", uid=None).stdout.strip()
    assert out == "t"
