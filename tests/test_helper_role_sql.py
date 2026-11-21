"""Migration 0039 (M10 ticket 1.26, issue #451): the helpers' narrow key path.

The audit's finding (S1): the price relay, the notice publisher and scripts hold the service key, with which a
process can insert any command, take the lease, write the engine's heartbeat or replace the owner row. Verified
before the change: scripts/price_stream.py and scripts/publish_notices.py read SUPABASE_SERVICE_KEY and nothing else
(the file checks below failed on main @ 453bb87: no other key path existed).

The file checks run everywhere. The SQL checks need a scratch Postgres with every migration applied
(KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise). In them the helper is what it is in
production: the `authenticated` role with the helper's user id."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M39 = ROOT / "migrations" / "0039_helper_role.sql"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0039 applied")
OWNER, HELPER, OTHER = (f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3))
NOTICE = {"id": "new_listing:ANT:2026-11-19", "kind": "new_listing", "flag": "ipo", "tier": "major",
          "session": "2026-11-19", "symbol": "ANT", "title": "Anthropic (ANT) starts trading", "starts": None,
          "first_eligible": "2027-11-19", "detail": {"name": "Anthropic"}, "available_ts": "2026-11-18T23:45:00Z",
          "withdrawn": False}


def _sql() -> str:
    return "\n".join(line for line in M39.read_text().splitlines() if not line.lstrip().startswith("--")).lower()


# ---------------------------------------------------------------- the files


def test_0039_is_additive_and_every_function_checks_the_helper():
    s = _sql()
    assert not re.search(r"\b(drop|truncate|alter function|delete from)\b", s)
    assert "enable row level security" in s
    assert "revoke all on kaibot.helper from public, anon, authenticated;" in s
    assert "revoke all on function kaibot.is_helper() from public, anon, authenticated;" in s
    fns = re.findall(r"create or replace function public\.(kb_helper_\w+)\(", s)
    assert sorted(fns) == ["kb_helper_heartbeat", "kb_helper_publish_notices", "kb_helper_trades", "kb_helper_whoami"]
    for body in re.split(r"create or replace function ", s)[1:]:
        if body.startswith("public.kb_helper_") and not body.startswith("public.kb_helper_whoami"):
            assert "if not kaibot.is_helper() then raise exception 'not authorised'" in body, body[:60]
        assert "security definer set search_path = ''" in body
    assert s.count("from public, anon;") == 5 and "to anon" not in s
    # the helper never writes these, by any function of the file
    for table in ("command", "engine_lease", "owner", "halt", "run", "ledger", "order", "config"):
        assert not re.search(rf"(insert into|update|delete from)\s+kaibot\.{table}\b", s), table
    assert "p_process not in ('price_stream')" in s and "'engine'" not in s and "'watchdog'" not in s
    assert "for insert to authenticated" in s and "'kb:prices'" in s and "extension = 'broadcast'" in s


def test_0039_header_claims_no_status():
    head = [ln for ln in M39.read_text().splitlines() if ln.startswith("--")]
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in head)


def test_the_relay_and_the_publisher_never_read_the_service_key_on_the_helper_path():
    """With the helpers' env file, neither script touches SUPABASE_SERVICE_KEY: the read is only in the fallback
    branch, after `helper_env(...)` returned None."""
    relay = (ROOT / "scripts" / "price_stream.py").read_text()
    build = relay[relay.index("def build_http"):relay.index("def main()")]
    helper_branch = build[:build.index("load_env_file(env_file)")]
    assert "helper_env(" in helper_branch and "SERVICE" not in helper_branch and "os.environ" not in helper_branch
    pub = (ROOT / "scripts" / "publish_notices.py").read_text()
    main = pub[pub.index("def main("):]
    assert main.index("helper_env(") < main.index("SUPABASE_SERVICE_KEY")
    assert "SERVICE" not in pub[pub.index("def publish_as_helper"):pub.index("def main(")]
    client = (ROOT / "src" / "kaibot" / "common" / "helper_session.py").read_text()
    assert "kaibot.engine" not in client  # the helpers' client imports nothing of the engine


# ---------------------------------------------------------------- the database


def psql(sql: str, uid: str = "", topic: str = "", role: str | None = "authenticated") -> str:
    pre = f"set test.uid = '{uid}'; set test.topic = '{topic}';\n" + (f"set role {role};\n" if role else "")
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


def refused(sql: str, match: str, **kw) -> None:
    with pytest.raises(AssertionError, match=match):
        psql(sql, **kw)


@pytest.fixture
def seeded():
    psql(f"""insert into auth.users (id) values ('{OWNER}'), ('{HELPER}'), ('{OTHER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;
      insert into kaibot.helper (user_id, name) values ('{HELPER}', 'mac helpers') on conflict do nothing;
      delete from kaibot.market_notice; delete from realtime.messages;
      delete from kaibot.heartbeat where process in ('price_stream', 'engine');
      insert into kaibot.heartbeat (process, ts, detail) values ('engine', '2026-11-20T20:00:00Z', '{{}}');""",
         role=None)


@needs_pg
def test_the_helper_cannot_insert_a_command_or_touch_the_lease_or_any_engine_table(seeded):
    """The ticket's check: the relay's key cannot insert a command or touch the lease."""
    refused("insert into kaibot.command (kind, payload) values ('new_run', '{}');", "permission denied", uid=HELPER)
    for sql in ("select * from kaibot.engine_lease;", "update kaibot.engine_lease set holder_id = 'x';",
                "delete from kaibot.engine_lease;", "select * from kaibot.command;",
                "update kaibot.heartbeat set ts = now() where process = 'engine';",
                "insert into kaibot.heartbeat (process, ts) values ('engine', now());",
                "insert into kaibot.owner (user_id) values ('" + HELPER + "');", "delete from kaibot.owner;",
                "insert into kaibot.halt (kind, reason) values ('x', 'y');", "select * from kaibot.trade;",
                "insert into kaibot.helper (user_id, name) values ('" + OTHER + "', 'x');",
                "select * from kaibot.helper;", "insert into kaibot.market_notice (id) values ('x');"):
        refused(sql, "permission denied", uid=HELPER)
    # the dashboard's owner-only functions refuse it too, the command function included
    fn = psql("select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
              "where n.nspname = 'public' and p.proname = 'kb_command' limit 1;", role=None)
    assert fn.startswith("kb_command(")
    refused("select public.kb_runs();", "not authorised", uid=HELPER)
    refused("select kaibot.is_helper();", "permission denied", uid=HELPER)
    lease_fns = psql("select string_agg(p.oid::regprocedure::text, ';') from pg_proc p join pg_namespace n on "
                     "n.oid = p.pronamespace where n.nspname in ('kaibot', 'public') and p.proname like '%lease%' "
                     "and has_function_privilege('authenticated', p.oid, 'execute');", role=None)
    assert lease_fns == ""  # no lease function is callable by a signed-in user


@needs_pg
def test_the_helper_writes_only_its_own_heartbeat_row(seeded):
    assert psql("select public.kb_helper_whoami();", uid=HELPER) == '{"helper": true}'
    psql("""select public.kb_helper_heartbeat('price_stream', '{"state": "streaming"}');""", uid=HELPER)
    row = psql("select detail->>'state', ts > now() - interval '1 minute' from kaibot.heartbeat "
               "where process = 'price_stream';", role=None)
    assert row == "streaming|t"
    for process in ("engine", "watchdog", "", "price_stream2"):
        refused(f"select public.kb_helper_heartbeat('{process}', '{{}}');", "not a helper process", uid=HELPER)
    refused("select public.kb_helper_heartbeat(null, '{}');", "not a helper process", uid=HELPER)
    refused("""select public.kb_helper_heartbeat('price_stream', '[1]');""", "detail must be an object", uid=HELPER)
    assert psql("select ts from kaibot.heartbeat where process = 'engine';", role=None).startswith("2026-11-20")


@needs_pg
def test_nobody_else_is_a_helper(seeded):
    for uid in (OWNER, OTHER, ""):
        assert psql("select public.kb_helper_whoami();", uid=uid) == '{"helper": false}'
        refused("select public.kb_helper_heartbeat('price_stream', '{}');", "not authorised", uid=uid)
        refused("select public.kb_helper_publish_notices('[]', '2026-11-18', '2026-11-25');", "not authorised",
                uid=uid)
        refused("select public.kb_helper_trades();", "not authorised", uid=uid)
    for fn in ("kb_helper_whoami()", "kb_helper_heartbeat(text, jsonb)", "kb_helper_trades(bigint, text, bigint, int)",
               "kb_helper_publish_notices(jsonb, date, date)", "kb_price_stream_sender()"):
        assert psql(f"select has_function_privilege('anon', 'public.{fn}', 'execute');", role=None) == "f"
    # an owner listed as a helper by mistake is still not one: the dashboard's sign-in gains nothing
    psql(f"insert into kaibot.helper (user_id, name) values ('{OWNER}', 'mistake');", role=None)
    assert psql("select public.kb_helper_whoami();", uid=OWNER) == '{"helper": false}'
    psql(f"delete from kaibot.helper where user_id = '{OWNER}';", role=None)


@needs_pg
def test_the_helper_publishes_and_withdraws_notices_as_the_job_did(seeded):
    other = {**NOTICE, "id": "big_day:fed_day:2026-11-20", "kind": "big_day", "flag": "fed_day", "symbol": None,
             "session": "2026-11-20", "title": "The Fed", "first_eligible": None, "detail": {}}
    far = {**NOTICE, "id": "new_listing:FAR:2027-03-01", "symbol": "FAR", "session": "2027-03-01"}
    call = "select public.kb_helper_publish_notices('{}'::jsonb, '2026-11-18', '2026-11-25');"
    psql(f"insert into kaibot.market_notice (id, kind, flag, tier, session, symbol, title, available_ts) values "
         f"('{far['id']}', 'new_listing', 'ipo', 'major', '2027-03-01', 'FAR', 'Far', now());", role=None)
    res = json.loads(psql(call.format(json.dumps([NOTICE, other])), uid=HELPER))
    assert res == {"written": 2, "withdrawn": []}
    res = json.loads(psql(call.format(json.dumps([{**NOTICE, "tier": "notice"}])), uid=HELPER))
    assert res == {"written": 1, "withdrawn": [other["id"]]}
    rows = psql("select id, tier, withdrawn from kaibot.market_notice order by id;", role=None).splitlines()
    assert rows == [f"{other['id']}|major|t", f"{NOTICE['id']}|notice|f", f"{far['id']}|major|f"]  # outside: kept
    refused(call.format(json.dumps([{**NOTICE, "kind": "nonsense"}])), "check constraint", uid=HELPER)
    refused("select public.kb_helper_publish_notices('[]', '2026-01-01', '2026-12-31');", "at most 60 days",
            uid=HELPER)
    refused("select public.kb_helper_publish_notices('{}', '2026-11-18', '2026-11-25');", "must be an array",
            uid=HELPER)


@needs_pg
def test_the_helper_reads_closed_trades_and_nothing_else_of_them(seeded):
    out = json.loads(psql("select public.kb_helper_trades(null, null, 0, 5);", uid=HELPER))
    assert out == [] or set(out[0]) == {"id", "run_id", "strategy", "symbol", "entry_ts", "exit_ts", "risk_usd",
                                        "pnl_usd", "exit_reason"}


@needs_pg
def test_only_the_helper_may_send_on_kb_prices_and_only_there(seeded):
    send = ("insert into realtime.messages (topic, extension, event, payload) values "
            "('{}', '{}', 'prices', '{{}}');")
    psql(send.format("kb:prices", "broadcast"), uid=HELPER, topic="kb:prices")
    assert psql("select count(*) from realtime.messages;", role=None) == "1"
    refused(send.format("kb:prices", "presence"), "row-level security", uid=HELPER, topic="kb:prices")
    refused(send.format("other", "broadcast"), "row-level security", uid=HELPER, topic="other")
    for uid in (OWNER, OTHER, ""):
        refused(send.format("kb:prices", "broadcast"), "row-level security", uid=uid, topic="kb:prices")
    refused(send.format("kb:prices", "broadcast"), "permission denied|row-level security", uid=HELPER,
            topic="kb:prices", role="anon")
    # 0032 is unchanged: the owner still receives, the helper does not
    probe = "select count(*) from realtime.messages where topic = realtime.topic();"
    assert psql(probe, uid=OWNER, topic="kb:prices") == "1" and psql(probe, uid=HELPER, topic="kb:prices") == "0"
