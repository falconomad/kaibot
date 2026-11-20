"""Migration 0032: only the owner receives the streamed prices on the private Realtime channel `kb:prices`.

The file checks run everywhere. The SQL checks need a scratch Postgres with every migration applied
(KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one, with stand-ins for realtime.messages and realtime.topic();
skipped otherwise)."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M32 = ROOT / "migrations" / "0032_price_stream_channel.sql"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0032 applied")
# Realtime checks a join the way this probe does: the rows of the joined topic the policy lets the user see.
PROBE = "select count(*) from realtime.messages where topic = realtime.topic();"
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def _sql() -> str:
    return "\n".join(line for line in M32.read_text().splitlines() if not line.lstrip().startswith("--")).lower()


def test_0032_is_additive_and_owner_only():
    s = _sql()
    assert not re.search(r"\b(delete|drop|truncate|alter table|insert|update)\b", s)
    assert "for select to authenticated" in s and "for insert" not in s
    assert "'kb:prices'" in s and "extension = 'broadcast'" in s and "kaibot.is_owner()" in s
    assert "revoke all on function public.kb_price_stream_reader() from public, anon;" in s


def psql(sql: str, uid: str = "", topic: str = "", role: str | None = "authenticated"):
    pre = f"set test.uid = '{uid}'; set test.topic = '{topic}';\n" + (f"set role {role};\n" if role else "")
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


@pytest.fixture
def seeded():
    psql(f"""insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;
      delete from realtime.messages;
      insert into realtime.messages (topic, extension, event, payload) values
        ('kb:prices', 'broadcast', 'prices', '{{"feed": "iex"}}'), ('kb:prices', 'presence', null, '{{}}'),
        ('other', 'broadcast', 'x', '{{}}');""", role=None)


@needs_pg
def test_the_owner_receives_the_broadcasts_on_kb_prices_only(seeded):
    assert psql(PROBE, uid=OWNER, topic="kb:prices") == "1"
    assert psql(PROBE, uid=OWNER, topic="other") == "0"


@needs_pg
def test_no_one_else_does(seeded):
    assert psql(PROBE, uid=OTHER, topic="kb:prices") == "0"
    assert psql(PROBE, uid="", topic="kb:prices") == "0"
    assert psql(PROBE, uid=OWNER, topic="kb:prices", role="anon") == "0"


@needs_pg
def test_applying_it_twice_is_harmless():
    sql = M32.read_text()
    psql(sql, role=None)
    assert psql("select count(*) from pg_policies where schemaname = 'realtime' and policyname = "
                "'kb owner receives prices';", role=None) == "1"
