"""Migration 0043: kaibot.headline, its retention, RLS, the licence guard and kb_headlines.

The file checks run everywhere. The SQL checks need a scratch Postgres with every migration applied
(KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M43 = ROOT / "migrations" / "0043_headline_feed.sql"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0043 applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"


def _sql() -> str:
    return "\n".join(ln for ln in M43.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


def test_0043_is_additive_and_owner_only():
    s = _sql()
    assert not re.search(r"\b(drop|truncate|alter function)\b", s)
    assert not re.search(r"\bupdate\s+kaibot\.", s) and "insert into" not in s
    # the only deletes are the retention trigger's, on this table
    assert re.findall(r"\bdelete from ([a-z_.]+)", s) == ["kaibot.headline", "kaibot.headline"]
    assert "enable row level security" in s
    assert "revoke all on kaibot.headline from public, anon, authenticated;" in s
    assert "grant select" not in s.replace("grant select, insert, update on kaibot.headline to service_role", "")
    assert "if not v_owner then raise exception 'not authorised'" in s
    assert "revoke all on function kaibot.headline_list(boolean, int) from public, anon, authenticated;" in s
    assert "revoke all on function public.kb_headlines(int) from public, anon;" in s
    assert "(p_owner is true or x.licence = 'public')" in s


def test_0043_ties_the_licence_to_the_source():
    s = _sql()
    assert "check (licence = case source when 'sec_edgar' then 'public' else 'owner_only' end)" in s
    # the same mapping as the engine's
    from kaibot.news.feed import LICENCES
    assert LICENCES == {"alpaca_benzinga": "owner_only", "sec_edgar": "public"}


def test_0043_header_claims_no_status():
    head = [ln for ln in M43.read_text().splitlines() if ln.startswith("--")]
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in head)


def psql(sql: str, uid: str = "", role: str | None = "authenticated") -> str:
    pre = f"set test.uid = '{uid}';\n" + (f"set role {role};\n" if role else "")
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


def _row(key: str, source: str = "alpaca_benzinga", licence: str | None = None, focus: bool = False,
         age: str = "1 hour", sym: str = "AAPL", url: str | None = "https://example.com/a") -> str:
    licence = licence or ("public" if source == "sec_edgar" else "owner_only")
    u = f"'{url}'" if url else "null"
    return (f"insert into kaibot.headline (dedup_key, symbols, headline, source, licence, url, tags, focus, created_at)"
            f" values ('{key}', array['{sym}'], 'Headline {key}', '{source}', '{licence}', {u}, '{{}}', {focus},"
            f" now() - interval '{age}');")


@pytest.fixture
def seeded():
    psql(f"""insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;
      delete from kaibot.headline;
      {_row('alp:1')}{_row('alp:2', focus=True, age='3 hours', sym='MSFT')}
      {_row('sec:0000320193-26-000001', source='sec_edgar', url=None)}""", role=None)
    yield
    psql("delete from kaibot.headline;", role=None)


def _items(uid: str = OWNER) -> list[dict]:
    return json.loads(psql("select public.kb_headlines(20);", uid=uid))["items"]


@needs_pg
def test_the_owner_reads_both_licences_held_first(seeded):
    items = _items()
    assert [i["headline"] for i in items][0] == "Headline alp:2"  # focus first, though older
    assert {i["licence"] for i in items} == {"owner_only", "public"}
    assert set(items[0]) == {"id", "symbols", "headline", "source", "licence", "url", "tags", "focus", "created_at"}


@needs_pg
def test_the_licence_guard_never_gives_owner_only_rows_to_a_non_owner(seeded):
    out = json.loads(psql("select kaibot.headline_list(false, 50);", role=None))
    assert out and {i["licence"] for i in out} == {"public"} and {i["source"] for i in out} == {"sec_edgar"}
    out = json.loads(psql("select kaibot.headline_list(null, 50);", role=None))
    assert {i["licence"] for i in out} == {"public"}


@needs_pg
def test_only_the_owner_calls_and_nobody_reads_the_table(seeded):
    with pytest.raises(AssertionError, match="not authorised"):
        psql("select public.kb_headlines(20);", uid=OTHER)
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select public.kb_headlines(20);", role="anon")
    for uid in (OWNER, OTHER):
        with pytest.raises(AssertionError, match="permission denied"):
            psql("select count(*) from kaibot.headline;", uid=uid)
    with pytest.raises(AssertionError, match="permission denied"):
        psql("select kaibot.headline_list(true, 20);", uid=OWNER)


@needs_pg
def test_the_rls_policy_lets_only_the_owner_see_rows_when_a_grant_exists(seeded):
    """Second lock: even with a table grant (there is none), RLS shows rows to the owner only."""
    assert psql("select has_function_privilege('authenticated', 'kaibot.is_owner()', 'execute');", role=None) == "f"
    psql("grant usage on schema kaibot to authenticated; grant select on kaibot.headline to authenticated;"
         "grant execute on function kaibot.is_owner() to authenticated;", role=None)
    try:
        assert psql("select count(*) from kaibot.headline;", uid=OWNER) == "3"
        assert psql("select count(*) from kaibot.headline;", uid=OTHER) == "0"
    finally:
        psql("revoke select on kaibot.headline from authenticated; revoke usage on schema kaibot from authenticated;"
             "revoke execute on function kaibot.is_owner() from authenticated;", role=None)


@needs_pg
def test_an_alpaca_row_cannot_be_public_and_urls_are_https(seeded):
    with pytest.raises(AssertionError, match="headline_licence_by_source"):
        psql(_row("alp:9", licence="public"), role=None)
    with pytest.raises(AssertionError, match="check constraint"):
        psql(_row("sec:0000320193-26-000009", source="sec_edgar", licence="owner_only"), role=None)
    with pytest.raises(AssertionError, match="check constraint"):
        psql(_row("alp:10", url="http://example.com/a"), role=None)
    with pytest.raises(AssertionError, match="check constraint"):
        psql(_row("alp:11", url="javascript:alert(1)"), role=None)


@needs_pg
def test_retention_keeps_7_days_and_500_rows(seeded):
    psql(_row("alp:old", age="8 days"), role=None)
    assert psql("select count(*) from kaibot.headline where dedup_key = 'alp:old';", role=None) == "0"
    psql("""insert into kaibot.headline (dedup_key, symbols, headline, source, licence, created_at)
      select 'alp:n' || g, array['AAPL'], 'Bulk ' || g, 'alpaca_benzinga', 'owner_only', now() - g * interval '1 second'
      from generate_series(1, 600) g;""", role=None)
    assert psql("select count(*) from kaibot.headline;", role=None) == "500"
    assert psql("select count(*) from kaibot.headline where dedup_key = 'alp:n1';", role=None) == "1"  # newest kept


@needs_pg
def test_the_engine_upsert_is_idempotent(seeded):
    psql("""insert into kaibot.headline (dedup_key, symbols, headline, source, licence, created_at)
      values ('alp:1', array['AAPL'], 'Headline alp:1', 'alpaca_benzinga', 'owner_only', now())
      on conflict (dedup_key) do update set focus = excluded.focus;""", role="service_role")
    assert psql("select count(*) from kaibot.headline where dedup_key = 'alp:1';", role=None) == "1"
