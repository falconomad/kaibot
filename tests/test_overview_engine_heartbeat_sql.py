"""Migration 0049: kb_overview returns only the engine's heartbeat row (#723, M19 ticket 8.5 of the second audit).

The file checks run everywhere: the new body is 0007's with exactly the one heartbeat line changed. The SQL checks
need the scratch Postgres of tests/pg_scratch.py (KAIBOT_TEST_PG): the answer's `heartbeat` is the engine's row alone,
for the owner only, and the payload it no longer carries is measured."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_invites_sql import OWNER, STRANGER, db, needs_pg, psql  # noqa: F401  (db: the fixture)

ROOT = Path(__file__).resolve().parents[1]
M07 = ROOT / "migrations" / "0007_m2_dashboard.sql"
M49 = ROOT / "migrations" / "0049_overview_engine_heartbeat.sql"


def _body(path: Path) -> list[str]:
    s = path.read_text()
    i = s.index("create or replace function public.kb_overview")
    return [ln for ln in s[i:s.index("end $$;", i)].splitlines() if not ln.lstrip().startswith("--")]


def test_0049_is_0007s_kb_overview_with_one_line_changed():
    old, new = _body(M07), _body(M49)
    assert len(old) == len(new)
    diff = [(a, b) for a, b in zip(old, new, strict=True) if a != b]
    assert diff == [("    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h)) from kaibot.heartbeat h), '[]'::jsonb),",
                     "    'heartbeat', coalesce((select jsonb_agg(to_jsonb(h)) from kaibot.heartbeat h "
                     "where h.process = 'engine'), '[]'::jsonb),")]


def test_0049_is_additive():
    s = "\n".join(ln for ln in M49.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()
    assert not re.search(r"\b(drop|truncate|alter|delete from|insert into|update)\b", s)
    assert re.findall(r"create or replace function (\w+\.\w+)\(", s) == ["public.kb_overview"]
    assert "revoke all on function public.kb_overview(bigint) from public, anon;" in s
    assert "grant execute on function public.kb_overview(bigint) to authenticated;" in s
    assert " to anon" not in s
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in M49.read_text().splitlines())


# what reports to kaibot.heartbeat besides the engine (engine/__main__.py, engine/watchdog.py, the runner): rows of
# the size they write, so the measured saving is of the right order
OTHERS = {
    "watchdog": {"state": "ok", "host": "mac", "engine_age_s": 4.1,
                 "checks": [{"name": f"c{i}", "ok": True} for i in range(12)]},
    "engine-check": {"state": "ok", "host": "mac", "pid": 4242, "lease": {"holder": "engine", "epoch": 17}},
    "research-runner": {"state": "idle", "host": "mac", "jobs": [{"id": f"job-{i}", "status": "done", "minutes": 12}
                                                                  for i in range(20)]},
}
ENGINE = {"state": "trading", "host": "mac", "next_open": "2026-11-27T09:30:00-05:00",
          "multi_day": {"v": 1, "rules": ["trend_momentum"], "held": 7}}


def _seed() -> str:
    rows = [("engine", ENGINE), *OTHERS.items()]
    values = ", ".join(f"('{p}', now(), '{json.dumps(d)}'::jsonb)" for p, d in rows)
    return f"insert into kaibot.heartbeat (process, ts, detail) values {values};"


@needs_pg
def test_kb_overview_returns_only_the_engines_row_and_the_payload_is_measured(db):  # noqa: F811
    out = psql(f"""begin;
      delete from kaibot.heartbeat;
      {_seed()}
      set local test.uid = '{OWNER}'; set local role authenticated;
      select public.kb_overview(-1)::text;
      reset role;
      select octet_length(coalesce(jsonb_agg(to_jsonb(h)), '[]'::jsonb)::text) from kaibot.heartbeat h;
      select octet_length(coalesce(jsonb_agg(to_jsonb(h)), '[]'::jsonb)::text) from kaibot.heartbeat h
       where h.process = 'engine';
      rollback;""", role=None).splitlines()
    answer, every, engine = json.loads(out[-3]), int(out[-2]), int(out[-1])
    assert [h["process"] for h in answer["heartbeat"]] == ["engine"]
    assert answer["heartbeat"][0]["detail"] == ENGINE
    assert set(answer) == {"run", "daily", "heartbeat", "pending_commands", "ledger", "today", "segments",
                           "halts_today"}
    # the answer's heartbeat is the engine's row exactly; the others' bytes are gone from every poll
    assert len(json.dumps(answer["heartbeat"], separators=(", ", ": "))) <= engine + 2
    print(f"heartbeat in kb_overview: {every} bytes before 0049, {engine} after ({every - engine} fewer per poll)")
    assert every - engine > 1000


@needs_pg
def test_kb_overview_without_an_engine_row_is_an_empty_list_and_stays_owner_only(db):  # noqa: F811
    out = psql(f"""begin;
      delete from kaibot.heartbeat;
      insert into kaibot.heartbeat (process, ts, detail) values ('watchdog', now(), '{{}}');
      set local test.uid = '{OWNER}'; set local role authenticated;
      select (public.kb_overview(-1) -> 'heartbeat')::text;
      rollback;""", role=None).splitlines()
    assert out[-1] == "[]"
    with pytest.raises(AssertionError, match="not authorised"):
        psql("select public.kb_overview(-1);", uid=STRANGER)
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select public.kb_overview(-1);", role="anon")
