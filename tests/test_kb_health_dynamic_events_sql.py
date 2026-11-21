"""Migration 0036 (kb_health's event list): the health events the engine sends under a dynamic name reach the
dashboard through kb_health's `events`: `multi_day_column_missing` (#410, the replicator's fallback), the pre-open
review's `multi_day_review` (#131) and `command_accept_reconcile` (S78).

The file checks run everywhere: 0036's function is 0033's, byte for byte, with only the new types added, and it keeps
0021's revoke/grant. tests/test_kb_health_engine_events_sql.py checks that the engine still sends every listed type,
dynamic names included. The SQL checks need a scratch Postgres with every migration applied (KAIBOT_TEST_PG; CI and
KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_kb_health_engine_events_sql import _emitted
from test_kb_health_news_events_sql import OTHER, PG, _sql, _types, owner, psql  # noqa: F401 (owner: autouse)

ROOT = Path(__file__).resolve().parents[1]
M33 = ROOT / "migrations" / "0033_kb_health_engine_health_events.sql"
M36 = ROOT / "migrations" / "0036_kb_health_dynamic_events.sql"
NEW = ("multi_day_column_missing", "multi_day_review", "command_accept_reconcile")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0036 applied")


def test_0036_is_0033_with_only_the_new_types_added():
    old, new = _sql(M33), _sql(M36)
    assert _types(new) == _types(old) + list(NEW)
    head_old, tail_old = old.split("where type in (", 1)[0], old.split("order by ts desc limit 80", 1)[1]
    head_new, tail_new = new.split("where type in (", 1)[0], new.split("order by ts desc limit 80", 1)[1]
    assert (head_new, tail_new) == (head_old, tail_old)
    assert "revoke all on function public.kb_health() from public, anon;" in tail_new
    assert "grant execute on function public.kb_health() to authenticated;" in tail_new


def test_0036_is_the_latest_definition_of_kb_health():
    later = sorted(p.name for p in (ROOT / "migrations").glob("*.sql")
                   if p.name > M36.name and "function public.kb_health()" in p.read_text())
    assert later == []


def test_0036_has_no_destructive_statement():
    s = _sql(M36).lower()
    assert not re.search(r"\b(delete|drop|truncate|alter table|insert|update)\b", s)


def test_the_engine_still_sends_every_type_0036_adds():
    assert set(NEW) <= _emitted(), sorted(set(NEW) - _emitted())


def test_the_header_claims_no_status():
    assert not re.search(r"^--\s*(NOT APPLIED|APPLIED)\b", M36.read_text(), re.M)


INSERT = """insert into kaibot.event (ts, type, payload, hash) values
  (now() - interval '3 minutes', 'multi_day_column_missing', '{"seq": 7}', 't0036-a'),
  (now() - interval '2 minutes', 'multi_day_review', '{"status": "timeout"}', 't0036-b'),
  (now() - interval '1 minute', 'multi_day_shorting_on', '{"action": "held"}', 't0036-c'),
  (now(), 'multi_day_fill', '{"symbol": "AAPL"}', 't0036-d');"""


@needs_pg
def test_kb_health_returns_the_new_events_and_0033s_and_still_not_the_routine_ones():
    out = psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;").stdout.strip().splitlines()
    got = [e for e in json.loads(out[-1])["events"]
           if e["payload"] and e["type"] in ("multi_day_column_missing", "multi_day_review", "multi_day_shorting_on",
                                             "multi_day_fill")]
    assert [e["type"] for e in got] == ["multi_day_shorting_on", "multi_day_review", "multi_day_column_missing"]


@needs_pg
def test_kb_health_stays_owner_only():
    r = psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;", OTHER, check=False)
    assert r.returncode and "not authorised" in r.stderr


@needs_pg
def test_the_read_only_check_of_migrations_pending_passes():
    out = psql("""select prosrc like '%multi_day_column_missing%' and prosrc like '%''multi_day_review''%'
                     and prosrc like '%multi_day_shorting_on%' from pg_proc where proname = 'kb_health';""",
               uid=None).stdout.strip()
    assert out == "t"
