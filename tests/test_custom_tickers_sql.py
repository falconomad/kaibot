"""M5 S68 (#96): the SQL side of custom ticker lists (migration 0013): kb_check_ticker, the named lists and the
accepted-ticker check on save/apply. Needs a scratch Postgres with the migrations applied
(KAIBOT_TEST_PG="-h /tmp -p 55432 -U postgres -d m5"); skipped otherwise (CI). The Python/SQL shape parity for
focus.custom is covered by test_config_sql.py::test_sql_and_python_validators_agree."""

from __future__ import annotations

import json
import os
import shlex
import subprocess

import pytest

PG = os.environ.get("KAIBOT_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0013 applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
SETUP = f"""
delete from kaibot.ticker_list; delete from kaibot.ticker_check; delete from kaibot.config_draft;
insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;
"""


def psql(sql: str, uid: str | None = OWNER, check: bool = True):
    pre = f"set test.uid = '{uid or ''}';\n"
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


def call(sql: str, uid: str | None = OWNER):
    out = psql(f"select {sql};", uid).stdout.strip().splitlines()
    return json.loads(out[-1])


def error(sql: str, uid: str | None = OWNER) -> str:
    r = psql(f"select {sql};", uid, check=False)
    assert r.returncode, "expected an error"
    return r.stderr


def answer(sym, status, etf=None, reason=None, days_ago=0):
    psql(f"""insert into kaibot.ticker_check (symbol, checked_on, status, etf, reason)
             values ('{sym}', (now() at time zone 'America/New_York')::date - {days_ago}, '{status}',
                     {f"'{etf}'" if etf else 'null'}, {f"'{reason}'" if reason else 'null'})
             on conflict (symbol, checked_on) do update set status = excluded.status, etf = excluded.etf,
               reason = excluded.reason""", uid=None)


@pytest.fixture(autouse=True)
def clean():
    psql(SETUP, uid=None)
    run = psql("select id from kaibot.run where status <> 'ended' order by id desc limit 1;", uid=None).stdout.strip()
    if not run:
        run = psql("insert into kaibot.run (amount_usd, level, status) values (5000, 'balanced', 'running') "
                   "returning id;", uid=None).stdout.strip()
    return int(run)


def test_check_ticker_is_owner_only_and_cached_for_the_day():
    assert "not authorised" in error("public.kb_check_ticker('PLTR')", OTHER)
    assert "not authorised" in error("public.kb_check_ticker('PLTR')", None)
    first = call("public.kb_check_ticker(' pltr ')")
    assert first["symbol"] == "PLTR" and first["status"] == "pending" and first["in_catalogue"] is False
    answer("PLTR", "accepted", "XLK")
    again = call("public.kb_check_ticker('PLTR')")
    assert again["status"] == "accepted" and again["etf"] == "XLK" and again["reason"] is None
    assert psql("select count(*) from kaibot.ticker_check;", None).stdout.strip() == "1"  # one row a day


def test_check_ticker_answers_without_a_vendor_call_when_it_can():
    assert call("public.kb_check_ticker('AAPL')") == {"symbol": "AAPL", "status": "accepted", "in_catalogue": True,
                                                      "reason": None}
    spy = call("public.kb_check_ticker('SPY')")
    assert spy["status"] == "refused" and "reference fund" in spy["reason"]
    for bad in ("", "TOOLONG", "PL TR", "123", "A;drop table x", None):
        arg = "null" if bad is None else "'" + bad + "'"
        got = call(f"public.kb_check_ticker({arg})")
        assert got["status"] == "refused" and "does not look like" in got["reason"]
    assert psql("select count(*) from kaibot.ticker_check;", None).stdout.strip() == "0"


def test_error_can_be_retried_and_refused_stays_refused_today():
    call("public.kb_check_ticker('UBER')")
    answer("UBER", "error", reason="We could not check UBER right now. Try again later.")
    assert call("public.kb_check_ticker('UBER')")["status"] == "pending"  # asking again re-queues it
    answer("UBER", "refused", reason="UBER cannot be traded at the broker right now.")
    assert call("public.kb_check_ticker('UBER')")["status"] == "refused"


def body(*pairs, mode="only", symbols=("AAPL", "MSFT", "NVDA")):
    return json.dumps({"focus": {"mode": mode, "symbols": list(symbols),
                                 "custom": {"name": "mine", "tickers": [{"symbol": s, "etf": e} for s, e in pairs]}}})


def test_save_and_apply_need_every_custom_ticker_accepted(clean):
    run = clean
    b = body(("PLTR", "XLK"), ("UBER", "XLI"))
    assert "PLTR has not been checked yet" in error(f"public.kb_save_config({run}, '{b}'::jsonb)")
    answer("PLTR", "accepted", "XLK")
    answer("UBER", "refused", reason="UBER trades about $2M a day.")
    assert "UBER cannot be used: UBER trades about $2M a day." in error(f"public.kb_save_config({run}, '{b}'::jsonb)")
    answer("UBER", "accepted", "XLY")
    assert "different sector fund" in error(f"public.kb_save_config({run}, '{b}'::jsonb)")  # etf comes from the check
    answer("UBER", "accepted", "XLI")
    assert call(f"public.kb_save_config({run}, '{b}'::jsonb)")["ok"] is True
    # refused between the draft and the apply: the apply is rejected, nothing is versioned
    answer("UBER", "refused", reason="UBER cannot be traded at the broker right now.")
    before = psql(f"select count(*) from kaibot.config where run_id = {run};", None).stdout.strip()
    assert "UBER cannot be used" in error(f"public.kb_apply_config({run}, gen_random_uuid())")
    assert psql(f"select count(*) from kaibot.config where run_id = {run};", None).stdout.strip() == before
    answer("UBER", "accepted", "XLI")
    v = call(f"public.kb_apply_config({run}, gen_random_uuid())")
    stored = psql(f"select body from kaibot.config where run_id = {run} and version = {v['version']};", None).stdout
    assert json.loads(stored)["focus"]["custom"]["tickers"] == [{"symbol": "PLTR", "etf": "XLK"},
                                                                {"symbol": "UBER", "etf": "XLI"}]
    # stored by value: a later refusal (or a deleted list) does not change the applied version
    answer("UBER", "refused", reason="x")
    assert json.loads(psql(f"select body from kaibot.config where run_id = {run} and version = {v['version']};",
                           None).stdout) == json.loads(stored)


def test_only_needs_five_and_a_week_old_check_is_too_old(clean):
    run = clean
    answer("PLTR", "accepted", "XLK")
    four = body(("PLTR", "XLK"), symbols=("AAPL", "MSFT", "NVDA"))
    assert "needs at least 5" in error(f"public.kb_save_config({run}, '{four}'::jsonb)")
    answer("UBER", "accepted", "XLI", days_ago=8)
    b = body(("PLTR", "XLK"), ("UBER", "XLI"))
    assert "UBER has not been checked yet" in error(f"public.kb_save_config({run}, '{b}'::jsonb)")
    forty_one = body(*[(f"Q{a}{c}", "SPY") for a in "ABCDEFG" for c in "ABCDEF"][:41])
    assert "at most 40" in error(f"public.kb_save_config({run}, '{forty_one}'::jsonb)")


def test_named_lists_are_per_owner():
    assert "not authorised" in error("public.kb_ticker_lists()", OTHER)
    assert "not authorised" in error("public.kb_save_ticker_list('x', array['PLTR'])", OTHER)
    assert "not authorised" in error("public.kb_delete_ticker_list('x')", None)
    saved = call("public.kb_save_ticker_list('  AI picks ', array['pltr', 'AAPL', ' uber', 'PLTR'])")
    assert saved == {"ok": True, "name": "AI picks", "symbols": ["PLTR", "AAPL", "UBER"]}
    answer("PLTR", "accepted", "XLK")
    got = call("public.kb_ticker_lists()")
    assert (got["max_tickers"], got["max_lists"], got["min_only"]) == (40, 20, 5)
    (lst,) = got["lists"]
    by = {s["symbol"]: s for s in lst["symbols"]}
    assert [s["symbol"] for s in lst["symbols"]] == ["PLTR", "AAPL", "UBER"]
    assert by["PLTR"]["status"] == "accepted" and by["PLTR"]["etf"] == "XLK"
    assert by["AAPL"] == {"symbol": "AAPL", "status": "accepted", "in_catalogue": True}
    assert by["UBER"]["status"] == "unchecked"
    call("public.kb_save_ticker_list('AI picks', array['SHOP'])")  # same name: replaced
    assert [s["symbol"] for s in call("public.kb_ticker_lists()")["lists"][0]["symbols"]] == ["SHOP"]
    # a row owned by someone else is never returned or deleted
    psql(f"insert into kaibot.ticker_list (owner, name, symbols) values ('{OTHER}', 'theirs', array['COIN']);", None)
    assert [x["name"] for x in call("public.kb_ticker_lists()")["lists"]] == ["AI picks"]
    call("public.kb_delete_ticker_list('theirs')")
    assert psql("select count(*) from kaibot.ticker_list where name = 'theirs';", None).stdout.strip() == "1"
    call("public.kb_delete_ticker_list('AI picks')")
    assert call("public.kb_ticker_lists()")["lists"] == []


def test_list_limits_and_messages():
    assert "1 to 40 characters" in error("public.kb_save_ticker_list('', array['PLTR'])")
    assert "1 to 40 characters" in error(f"public.kb_save_ticker_list('{'x' * 41}', array['PLTR'])")
    assert "at least one ticker" in error("public.kb_save_ticker_list('a', array[]::text[])")
    forty_one = ",".join(f"'Q{a}{c}'" for a in "ABCDEFG" for c in "ABCDEF")
    assert "at most 40 tickers" in error(f"public.kb_save_ticker_list('a', array[{forty_one}])")
    assert "does not look like" in error("public.kb_save_ticker_list('a', array['PLTR', 'not a ticker'])")
    assert "Reference funds" in error("public.kb_save_ticker_list('a', array['PLTR', 'SPY'])")
    for i in range(20):
        call(f"public.kb_save_ticker_list('list {i}', array['PLTR'])")
    assert "at most 20 lists" in error("public.kb_save_ticker_list('one more', array['PLTR'])")
    call("public.kb_save_ticker_list('list 3', array['UBER'])")  # updating an existing list still works


def test_browser_roles_have_no_table_access():
    for role in ("anon", "authenticated"):
        for tbl in ("ticker_check", "ticker_list"):
            r = psql(f"set role {role}; select count(*) from kaibot.{tbl};", None, check=False)
            assert r.returncode and "permission denied" in r.stderr
    r = psql("set role anon; select public.kb_check_ticker('PLTR');", None, check=False)
    assert r.returncode and "permission denied" in r.stderr
