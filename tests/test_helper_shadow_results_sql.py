"""Migration 0047 (#371): public.kb_helper_shadow_results(), the shadow results for the helper, read-only.

Product's decision on #371: the research runner runs the monthly shadow-results report without any write-capable
key. The helper (0038) calls this one function; it runs scripts/shadow_results.sql's four SELECTs.

The file checks run everywhere. The SQL checks need a scratch Postgres with every migration applied
(KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one, tests/pg_scratch.py; skipped otherwise). In them the helper
is what it is in production: the `authenticated` role with the helper's user id (auth.uid() reads test.uid)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
M47 = ROOT / "migrations" / "0047_helper_shadow_results.sql"
SQL = ROOT / "scripts" / "shadow_results.sql"
FIX = ROOT / "tests" / "fixtures" / "shadow_results"
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with every migration applied")
OWNER, HELPER, OTHER = (f"00000000-0000-0000-0000-00000000000{i}" for i in (1, 2, 3))
FN = "public.kb_helper_shadow_results()"


def _code() -> str:
    return "\n".join(ln for ln in M47.read_text().splitlines() if not ln.lstrip().startswith("--")).lower()


def _queries() -> list[str]:
    return [p.strip() for p in re.split(r"^-- Q\d\..*\n", SQL.read_text(), flags=re.M)[1:]]


# ---------------------------------------------------------------- the file


def test_0047_runs_the_sh_files_four_queries_byte_for_byte():
    text = M47.read_text()
    qs = _queries()
    assert len(qs) == 4
    for i, q in enumerate(qs, 1):
        copied = text.split(f"-- >>> Q{i} ", 1)[1].split("\n", 1)[1].split(f"\n-- <<< Q{i}\n", 1)[0]
        assert copied + ";" == q, f"Q{i} differs from scripts/shadow_results.sql"


def test_0047_is_one_definer_function_that_checks_the_helper_and_writes_nothing():
    s = _code()
    assert re.findall(r"create or replace function ([\w.]+)\(", s) == ["public.kb_helper_shadow_results"]
    assert "returns json\nlanguage plpgsql stable security definer set search_path = '' as $$" in s
    body = s.split("as $$", 1)[1].split("end $$;", 1)[0]
    assert body.lstrip().startswith("declare") and \
        body.index("if not kaibot.is_helper() then raise exception 'not authorised'") < body.index("select ")
    assert not re.search(r"\b(insert|update|delete|truncate|drop|alter|create\s+(table|policy|trigger|index)|copy|"
                         r"perform|execute|call|set\s+(role|session)|dblink|pg_sleep)\b", body)
    assert not re.search(r"\b(insert|update|delete|truncate|drop|alter)\b", s)
    # exactly one revoke from everyone and one grant, to authenticated; no table, schema or policy grant
    assert re.findall(r"^(?:revoke|grant)[^\n]*", s, flags=re.M) == [
        "revoke all on function public.kb_helper_shadow_results() from public, anon, authenticated, service_role;",
        "grant execute on function public.kb_helper_shadow_results() to authenticated;"]
    assert "create policy" not in s and "on schema" not in s


def test_0047_header_claims_no_status():
    head = [ln for ln in M47.read_text().splitlines() if ln.startswith("--")]
    assert not any(re.match(r"^--\s*(NOT )?APPLIED\b", ln) for ln in head)


# ---------------------------------------------------------------- the database


def psql(sql: str, uid: str = "", role: str | None = "authenticated", flags: tuple[str, ...] = ("-tA",)) -> str:
    pre = f"set test.uid = '{uid}';\n" + (f"set role {role};\n" if role else "")
    r = subprocess.run(["psql", *shlex.split(PG), "-X", *flags, "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
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
      insert into kaibot.helper (user_id, name) values ('{HELPER}', 'mac helpers') on conflict do nothing;""",
         role=None)


@needs_pg
def test_only_the_helper_can_call_it(seeded):
    assert json.loads(psql(f"select {FN};", uid=HELPER)).keys() == {"q1", "q2", "q3", "q4"}
    for uid in (OWNER, OTHER, ""):  # the owner, another signed-in user, a token without a user
        refused(f"select {FN};", "not authorised", uid=uid)
    for role in ("anon", "service_role"):  # even with the helper's user id
        refused(f"select {FN};", "permission denied", uid=HELPER, role=role)
    for role in ("public", "anon", "service_role"):
        assert psql(f"select has_function_privilege('{role}', '{FN}', 'execute');", role=None) == "f"
    assert psql(f"select has_function_privilege('authenticated', '{FN}', 'execute');", role=None) == "t"
    # no table, schema or policy right came with it: the helper still reads none of the rows directly
    assert psql("select has_schema_privilege('authenticated', 'kaibot', 'usage');", role=None) == "f"
    for t in ("event", "signal", "run"):
        assert psql(f"select has_table_privilege('authenticated', 'kaibot.{t}', 'select');", role=None) == "f"
        refused(f"select count(*) from kaibot.{t};", "permission denied", uid=HELPER)
    assert psql("select provolatile::text || prosecdef::text || array_to_string(proconfig, ',') from pg_proc "
                "where proname = 'kb_helper_shadow_results';", role=None) == "struesearch_path=\"\""


@needs_pg
def test_it_writes_nothing(seeded):
    """In a read-only transaction the call succeeds, and afterwards the transaction still has no transaction id:
    Postgres assigns one at the first write of any kind (a row, a sequence, a temp table), so none happened."""
    out = psql(f"""begin; set transaction read only;
      select count(*) from kaibot.event;
      set local role authenticated; set local test.uid = '{HELPER}';
      select {FN} is not null;
      reset role;
      select txid_current_if_assigned() is null;
      select count(*) from kaibot.event;
      commit;""", role=None)
    before, called, no_xid, after = out.splitlines()
    assert (called, no_xid, before) == ("t", "t", after)
    # and the server would refuse a write in it anyway: STABLE plpgsql runs its statements read-only
    assert psql("select provolatile from pg_proc where proname = 'kb_helper_shadow_results';", role=None) == "s"


def _fixture_sql() -> str:
    sys.path.insert(0, str(ROOT / "tests"))
    import test_shadow_results_sql as t  # the .sh's own fixture: two runs, a restart, a session without a summary

    return t.fixture()


@needs_pg
def test_on_the_shs_fixture_the_script_prints_what_the_sh_prints(seeded):
    """One rolled-back transaction: the fixture rows, the .sh's four queries in psql's aligned format, then the
    function as the helper; scripts/shadow_results.py's rendering of the answer equals psql's output byte for byte.
    The answer and the output also equal the unit test's fixture pair (tests/fixtures/shadow_results/)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import shadow_results as sr

    marks = "\n".join(f"\\echo @@Q{i}\n{q}" for i, q in enumerate(_queries(), 1))
    script = (_fixture_sql() + "\\pset pager off\n" + marks +
              f"\n\\echo @@RPC\n\\pset format unaligned\n\\pset tuples_only on\nset local role authenticated;\n"
              f"set local test.uid = '{HELPER}';\nselect {FN};\nrollback;\n")
    out = psql(script, role=None, flags=())
    tables, rpc = out.split("@@RPC\n")
    printed = "".join(p.split("\n", 1)[1] for p in tables.split("@@Q")[1:])
    ans = json.loads(rpc.strip())
    assert sr.render(ans) == printed
    assert sr.render(ans) == (FIX / "sh_output.txt").read_text()
    assert ans == json.loads((FIX / "rpc_answer.json").read_text())


@needs_pg
def test_a_multi_line_error_prints_as_psql_prints_it(seeded):
    """A shadow error whose text holds a newline: the script's table equals psql's, continuation mark and all."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import shadow_results as sr

    extra = ("insert into kaibot.event (ts, run_id, type, payload, hash) values ('2026-11-17 17:00+00', :r2, "
             "'shadow_error', jsonb_build_object('where', 'news', 'error', E'ValueError: bad\\nsecond line'), "
             "'shadow-nl');\n")
    q4 = _queries()[3]
    script = (_fixture_sql() + extra + "\\pset pager off\n\\echo @@Q4\n" + q4 +
              f"\n\\echo @@RPC\n\\pset format unaligned\n\\pset tuples_only on\nset local role authenticated;\n"
              f"set local test.uid = '{HELPER}';\nselect {FN};\nrollback;\n")
    out = psql(script, role=None, flags=())
    table, rpc = out.split("@@Q4\n", 1)[1].split("@@RPC\n")
    rows = json.loads(rpc.strip())["q4"]
    assert any("\n" in (r["detail"] or "") for r in rows)
    mine = sr.table(sr.COLUMNS["q4"], [[r[c] for c, _ in sr.COLUMNS["q4"]] for r in rows])
    assert mine.rstrip("\n") == table.rstrip("\n")
    assert "+\n" in mine
