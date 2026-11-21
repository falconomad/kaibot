"""Migration 0035 (#378): kaibot.market_notice and kb_market_notices, the dashboard's market notices.

The file checks run everywhere. The SQL checks need a scratch Postgres with every migration applied
(KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M35 = ROOT / "migrations" / "0035_market_notices.sql"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0035 applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def _sql() -> str:
    return "\n".join(line for line in M35.read_text().splitlines() if not line.lstrip().startswith("--")).lower()


def test_0035_is_additive_and_owner_only():
    s = _sql()
    assert not re.search(r"\b(delete|drop|truncate|alter function)\b", s)
    assert not re.search(r"\bupdate\s+kaibot\.", s) and "insert into" not in s
    assert "enable row level security" in s and "create policy" not in s
    assert "revoke all on kaibot.market_notice from public, anon, authenticated;" in s
    assert "if not kaibot.is_owner() then raise exception" in s
    assert "language plpgsql stable security definer set search_path = ''" in s
    assert "revoke all on function public.kb_market_notices(date) from public, anon;" in s


def test_0035_header_claims_no_status():
    head = [ln for ln in M35.read_text().splitlines() if ln.startswith("--")]
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in head)


def psql(sql: str, uid: str = "", role: str | None = "authenticated") -> str:
    pre = f"set test.uid = '{uid}';\n" + (f"set role {role};\n" if role else "")
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


ANT_ROW = {"id": "new_listing:ANT:2026-11-19", "kind": "new_listing", "flag": "ipo", "tier": "major",
           "session": "2026-11-19", "symbol": "ANT", "title": "Anthropic (ANT) starts trading",
           "first_eligible": "2027-11-19", "detail": {"name": "Anthropic", "status": "expected"},
           "available_ts": "2026-11-18T23:45:00Z"}


def _row_sql(r: dict) -> str:
    return (f"insert into kaibot.market_notice (id, kind, flag, tier, session, symbol, title, first_eligible, detail,"
            f" available_ts, withdrawn) values ('{r['id']}', '{r['kind']}', '{r['flag']}', '{r['tier']}', "
            f"'{r['session']}', {repr(r['symbol']) if r['symbol'] else 'null'}, '{r['title']}', "
            f"{repr(r['first_eligible']) if r.get('first_eligible') else 'null'}, '{json.dumps(r['detail'])}', "
            f"'{r['available_ts']}', {str(r.get('withdrawn', False)).lower()}) on conflict (id) do update set "
            f"withdrawn = excluded.withdrawn, tier = excluded.tier, available_ts = excluded.available_ts;")


@pytest.fixture
def seeded():
    fed = {**ANT_ROW, "id": "big_day:fed_day:2026-11-18", "kind": "big_day", "flag": "fed_day", "symbol": None,
           "session": "2026-11-18", "title": "The Fed", "first_eligible": None, "detail": {}}
    gone = {**ANT_ROW, "id": "new_listing:OLDX:2026-11-20", "symbol": "OLDX", "session": "2026-11-20",
            "withdrawn": True}
    later = {**ANT_ROW, "id": "new_listing:LATE:2026-11-20", "symbol": "LATE", "session": "2026-11-20",
             "available_ts": "2099-01-01T00:00:00Z"}
    far = {**ANT_ROW, "id": "new_listing:FAR:2026-12-31", "symbol": "FAR", "session": "2026-12-31"}
    alarm = {"session": "2026-11-19", "flags": {"fed_day": False, "cpi_day": False, "megacap_report": None,
             "index_reshuffle": False, "mega_listing": True}, "tier": "major", "n_megacaps": None, "megacap_major": 3,
             "details": {"index": [], "mega_listing": ["ANT"], "megacaps_reporting": []}}
    rows = "".join(_row_sql(r) for r in (ANT_ROW, fed, gone, later, far))
    psql(f"""insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;
      {rows}
      insert into kaibot.event (ts, type, payload, hash) select '2026-11-19 14:30+00', 'market_alarm',
        '{json.dumps(alarm)}'::jsonb, 'test-0035-alarm'
        where not exists (select 1 from kaibot.event where hash = 'test-0035-alarm');""", role=None)


def notices(today: str = "2026-11-19") -> dict:
    return json.loads(psql(f"select public.kb_market_notices('{today}');", uid=OWNER))


@needs_pg
def test_the_19_november_window_has_ant_and_the_engines_mega_listing(seeded):
    out = notices()
    assert out["today"] == "2026-11-19" and out["from"] == "2026-11-13" and out["to"] == "2026-11-27"
    assert out["sessions"] == ["2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"]
    got = {(n["kind"], n["flag"], n["session"], n["symbol"]): n for n in out["notices"]}
    ant = got[("new_listing", "ipo", "2026-11-19", "ANT")]
    assert (ant["tier"], ant["first_eligible"], ant["source"]) == ("major", "2027-11-19", "publisher")
    big = got[("big_day", "mega_listing", "2026-11-19", "ANT")]
    assert (big["tier"], big["source"], big["starts"]) == ("major", "engine", None)
    assert ("big_day", "fed_day", "2026-11-18", None) in got
    syms = {n["symbol"] for n in out["notices"]}
    assert not syms & {"OLDX", "LATE", "FAR"}  # withdrawn, not yet available, outside the window


@needs_pg
def test_the_engines_event_wins_over_the_publishers_row(seeded):
    dup = {**ANT_ROW, "id": "big_day:mega_listing:2026-11-19:ANT", "kind": "big_day", "flag": "mega_listing",
           "tier": "notice", "first_eligible": None, "detail": {}}
    psql(_row_sql(dup), role=None)
    rows = [n for n in notices()["notices"] if n["flag"] == "mega_listing"]
    assert len(rows) == 1 and rows[0]["source"] == "engine" and rows[0]["tier"] == "major"


@needs_pg
def test_a_fed_day_from_the_engine_starts_at_14_00_new_york(seeded):
    alarm = {"session": "2026-12-09", "flags": {"fed_day": True, "cpi_day": False, "megacap_report": False,
             "index_reshuffle": False, "mega_listing": False}, "tier": "major", "details": {}}
    psql(f"""insert into kaibot.event (ts, type, payload, hash) select '2026-12-09 14:30+00', 'market_alarm',
      '{json.dumps(alarm)}'::jsonb, 'test-0035-fed'
      where not exists (select 1 from kaibot.event where hash = 'test-0035-fed');""",
         role=None)
    fed = [n for n in notices("2026-12-09")["notices"] if n["flag"] == "fed_day"]
    assert len(fed) == 1 and fed[0]["tier"] == "major"
    assert datetime.fromisoformat(fed[0]["starts"]) == datetime(2026, 12, 9, 19, tzinfo=UTC)  # 14:00 EST


@needs_pg
def test_only_the_owner_reads_and_nobody_reads_the_table(seeded):
    with pytest.raises(AssertionError, match="not authorised"):
        psql("select public.kb_market_notices('2026-11-19');", uid=OTHER)
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select public.kb_market_notices('2026-11-19');", role="anon")
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select count(*) from kaibot.market_notice;", uid=OWNER)
