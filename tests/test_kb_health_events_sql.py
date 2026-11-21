"""Migration 0029 (kb_health's event list): the dashboard's unfilled orders (#333/#337), daily note (#311/#318) and
pre-open shadow verdicts (#310/#317) reach it through kb_health's `events`.

The file checks run everywhere: 0029's function is 0021's, byte for byte, with only the three types added, and it keeps
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
M21 = ROOT / "migrations" / "0021_kb_health_multiday_events.sql"
M29 = ROOT / "migrations" / "0029_kb_health_engine_followup_events.sql"
NEW = ("multi_day_unfilled", "daily_note", "multi_day_review_shadow")
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0029 applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def _sql(path: Path) -> str:
    """The statements of a migration file, without its comment lines."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("--")).strip()


def _types(sql: str) -> list[str]:
    body = sql.split("where type in (", 1)[1].split("order by ts desc limit 80", 1)[0]
    return re.findall(r"'([a-z_]+)'", body)


def test_0029_is_0021_with_only_the_three_types_added():
    old, new = _sql(M21), _sql(M29)
    assert _types(new) == _types(old) + list(NEW)
    # Everything outside the type list is 0021's text, grants and revokes included.
    head_old, tail_old = old.split("where type in (", 1)[0], old.split("order by ts desc limit 80", 1)[1]
    head_new, tail_new = new.split("where type in (", 1)[0], new.split("order by ts desc limit 80", 1)[1]
    assert (head_new, tail_new) == (head_old, tail_old)
    assert "revoke all on function public.kb_health() from public, anon;" in tail_new
    assert "grant execute on function public.kb_health() to authenticated;" in tail_new


def test_0029_is_superseded_only_by_0031_0033_and_0036():
    """0031 (the news poller's events), 0033 (the engine's health events) and 0036 (the dynamically named ones)
    re-create kb_health; each one's own test checks it against the one before."""
    later = sorted(p.name for p in (ROOT / "migrations").glob("*.sql")
                   if p.name > M29.name and "kb_health" in p.read_text())
    assert later == ["0031_kb_health_news_feed_events.sql", "0033_kb_health_engine_health_events.sql",
                     "0036_kb_health_dynamic_events.sql"]


def test_0029_has_no_destructive_statement():
    s = _sql(M29).lower()
    assert not re.search(r"\b(delete|drop|truncate|alter table|insert|update)\b", s)


def test_the_dashboard_reads_no_event_type_kb_health_leaves_out():
    """Every event type apps/web/src matches on (`type === "..."`, Health.tsx's EV labels) is in the list of the
    latest kb_health: the newest migration that re-creates it (0031 since #354, 0033 once #401 is merged)."""
    latest = max(p for p in (ROOT / "migrations").glob("*.sql")
                 if "create or replace function public.kb_health()" in p.read_text())
    listed, read = set(_types(_sql(latest))), set()
    for f in (ROOT / "apps" / "web" / "src").rglob("*.ts*"):
        if ".test." in f.name:
            continue
        t = f.read_text()
        read |= set(re.findall(r'\btype\s*===\s*"([a-z_]+)"', t))
        m = re.search(r"const EV: Record<string, string> = \{(.*?)\n\};", t, re.S)
        if m:
            read |= set(re.findall(r"\b([a-z_]+):\s*\"", m.group(1)))
    assert read and sorted(read - listed) == []


def test_every_event_type_kb_health_returns_has_a_plain_word_label():
    """The reverse of the test above: Health.tsx shows every type the latest kb_health returns in words, never by its
    code name (EV, or a type it words elsewhere: halts, commands, the corporate-action events of lib/corpactions.ts)."""
    latest = max(p for p in (ROOT / "migrations").glob("*.sql")
                 if "create or replace function public.kb_health()" in p.read_text())
    web = ROOT / "apps" / "web" / "src"
    health = (web / "pages" / "Health.tsx").read_text()
    ev = re.search(r"const EV: Record<string, string> = \{(.*?)\n\};", health, re.S)
    labelled = set(re.findall(r"\b([a-z_]+):\s*\"", ev.group(1)))
    labelled |= set(re.findall(r'\be\.type === "([a-z_]+)"', (web / "lib" / "corpactions.ts").read_text()))
    labelled.add("halt")  # eventText: "Halt: <the halt's text>"
    explain = (web / "lib" / "explain.ts").read_text()
    commands = re.search(r"const COMMAND_TEXT: Record<string, string> = \{(.*?)\n\};", explain, re.S)
    labelled |= {f"command_{c}" for c in re.findall(r"\b([a-z_]+):\s*\"", commands.group(1))}
    assert sorted(set(_types(_sql(latest))) - labelled) == []


# ---------------------------------------------------------------- on a scratch Postgres


def psql(sql: str, uid: str | None = OWNER, check: bool = True):
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"],
                       input=f"set test.uid = '{uid or ''}';\n" + sql, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


INSERT = """insert into kaibot.event (ts, type, payload, hash) values
  (now() - interval '3 minutes', 'multi_day_unfilled', '{"symbol": "AAPL", "left": 3}', 't0029-a'),
  (now() - interval '2 minutes', 'daily_note', '{"text": "A quiet day."}', 't0029-b'),
  (now() - interval '1 minute', 'multi_day_review_shadow', '{"acted": false}', 't0029-c'),
  (now(), 'multi_day_fill', '{"symbol": "AAPL"}', 't0029-d');"""


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
def test_kb_health_returns_the_three_new_types_and_still_not_the_others():
    out = health_with_events().stdout.strip().splitlines()
    got = [e for e in json.loads(out[-1])["events"] if e["payload"] and e["type"] in (*NEW, "multi_day_fill")]
    assert [e["type"] for e in got] == ["multi_day_review_shadow", "daily_note", "multi_day_unfilled"]  # newest first
    assert got[1]["payload"] == {"text": "A quiet day."}


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
    out = psql("""select prosrc like '%multi_day_unfilled%' and prosrc like '%''daily_note''%'
                     and prosrc like '%multi_day_review_shadow%' from pg_proc where proname = 'kb_health';""",
               uid=None).stdout.strip()
    assert out == "t"
