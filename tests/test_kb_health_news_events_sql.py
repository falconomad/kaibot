"""Migration 0031 (kb_health's event list): the news poller's health events (#354, PR #355) reach the dashboard
through kb_health's `events`.

The file checks run everywhere: 0031's function is 0029's, byte for byte, with only the two types added, and it keeps
0021's revoke/grant. The SQL checks need a scratch Postgres with every migration applied (KAIBOT_TEST_PG; CI and
KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M29 = ROOT / "migrations" / "0029_kb_health_engine_followup_events.sql"
M31 = ROOT / "migrations" / "0031_kb_health_news_feed_events.sql"
NEW = ("news_feed_unhealthy", "news_feed_ok")
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0031 applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def _sql(path: Path) -> str:
    """The statements of a migration file, without its comment lines."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("--")).strip()


def _types(sql: str) -> list[str]:
    body = sql.split("where type in (", 1)[1].split("order by ts desc limit 80", 1)[0]
    return re.findall(r"'([a-z_]+)'", body)


def test_0031_is_0029_with_only_the_two_types_added():
    old, new = _sql(M29), _sql(M31)
    assert _types(new) == _types(old) + list(NEW)
    # Everything outside the type list is 0029's text, grants and revokes included.
    head_old, tail_old = old.split("where type in (", 1)[0], old.split("order by ts desc limit 80", 1)[1]
    head_new, tail_new = new.split("where type in (", 1)[0], new.split("order by ts desc limit 80", 1)[1]
    assert (head_new, tail_new) == (head_old, tail_old)
    assert "revoke all on function public.kb_health() from public, anon;" in tail_new
    assert "grant execute on function public.kb_health() to authenticated;" in tail_new


def test_every_later_definition_of_kb_health_keeps_the_news_events():
    """0033 redefines kb_health after 0031; every later definition must still list the two types."""
    later = sorted(p for p in (ROOT / "migrations").glob("*.sql")
                   if p.name > M31.name and "function public.kb_health()" in p.read_text())
    for p in later:
        assert set(NEW) <= set(_types(_sql(p))), p.name


def test_0031_has_no_destructive_statement():
    s = _sql(M31).lower()
    assert not re.search(r"\b(delete|drop|truncate|alter table|insert|update)\b", s)


def test_the_engine_sends_exactly_these_types():
    """The poller's event names (ingest/news_poll.py) are the two 0031 adds, and Health.tsx labels both."""
    src = (ROOT / "src" / "kaibot" / "ingest" / "news_poll.py")
    if src.exists():  # #355 may merge after this PR
        sent = set(re.findall(r'"(news_feed_[a-z_]+)"', src.read_text()))
        assert sent == set(NEW)
    health = (ROOT / "apps" / "web" / "src" / "pages" / "Health.tsx").read_text()
    m = re.search(r"const EV: Record<string, string> = \{(.*?)\n\};", health, re.S)
    assert m and all(re.search(rf"\b{t}:\s*\"", m.group(1)) for t in NEW)


# ---------------------------------------------------------------- on a scratch Postgres


def psql(sql: str, uid: str | None = OWNER, check: bool = True):
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"],
                       input=f"set test.uid = '{uid or ''}';\n" + sql, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


INSERT = """insert into kaibot.event (ts, type, payload, hash) values
  (now() - interval '3 minutes', 'news_feed_unhealthy', '{"state": "empty", "symbols": 75}', 't0031-a'),
  (now() - interval '2 minutes', 'news_feed_ok', '{"state": "ok"}', 't0031-b'),
  (now() - interval '1 minute', 'multi_day_review_shadow', '{"acted": false}', 't0031-c'),
  (now(), 'multi_day_fill', '{"symbol": "AAPL"}', 't0031-d');"""


def health_with_events(uid: str = OWNER, check: bool = True):
    """kb_health() as `uid`, with the test events inserted in the same transaction and rolled back after
    (kaibot.event is append-only)."""
    return psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;", uid, check)


@pytest.fixture(autouse=True)
def owner():
    if PG:
        psql(f"""insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
          insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""", uid=None)


@needs_pg
def test_kb_health_returns_the_news_events_and_0029s_and_still_not_the_others():
    out = health_with_events().stdout.strip().splitlines()
    got = [e for e in json.loads(out[-1])["events"]
           if e["payload"] and e["type"] in (*NEW, "multi_day_review_shadow", "multi_day_fill")]
    assert [e["type"] for e in got] == ["multi_day_review_shadow", "news_feed_ok", "news_feed_unhealthy"]
    assert got[2]["payload"] == {"state": "empty", "symbols": 75}


@needs_pg
def test_kb_health_stays_owner_only_and_not_for_anon():
    r = health_with_events(uid=OTHER, check=False)
    assert r.returncode and "not authorised" in r.stderr
    out = psql("""select p.prosecdef, p.provolatile, has_function_privilege('anon', p.oid, 'execute'),
                         has_function_privilege('authenticated', p.oid, 'execute')
                  from pg_proc p where p.oid = 'public.kb_health()'::regprocedure;""", uid=None).stdout.strip()
    assert out == "t|s|f|t"


@needs_pg
def test_the_read_only_check_of_migrations_pending_passes():
    out = psql("""select prosrc like '%news_feed_unhealthy%' and prosrc like '%news_feed_ok%'
                     and prosrc like '%multi_day_review_shadow%' from pg_proc where proname = 'kb_health';""",
               uid=None).stdout.strip()
    assert out == "t"
