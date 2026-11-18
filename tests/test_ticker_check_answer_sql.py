"""kb_ticker_check_answer (migration *_kb_ticker_check_answer.sql): the dashboard's Vercel function
(apps/web/api/check-ticker.ts) writes a ticker check's answer. Owner only; only today's pending or errored row;
validated input; a failed read counts an attempt and the third makes the row `error`, as tickercheck.process does.

The static checks always run. The SQL ones need a scratch Postgres with the migrations applied (KAIBOT_TEST_PG, or
KAIBOT_TEST_PG_AUTO=1 as in CI); skipped otherwise."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted((ROOT / "migrations").glob("*_kb_ticker_check_answer.sql"))
PG = os.environ.get("KAIBOT_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with the migrations applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
GOOD = {"sessions": 120, "adv20_usd": 3e8, "last_close": 42.5, "exchange": "NASDAQ", "as_of": "x",
        "checked_by": "vercel"}


# ------------------------------------------------------------------ static: dashboard-only, additive
def test_one_migration_after_the_engine_ones():
    assert len(FILES) == 1
    numbers = sorted(int(p.name[:4]) for p in (ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert len(numbers) == len(set(numbers)), "two migrations share a number"
    assert int(FILES[0].name[:4]) > 22


def test_it_only_adds_this_function():
    sql = re.sub(r"--[^\n]*", "", FILES[0].read_text()).lower()
    assert re.findall(r"create or replace function ([\w.]+)", sql) == ["public.kb_ticker_check_answer"]
    for word in ("create table", "alter ", "drop ", "truncate", "delete ", "insert ", "create index", "create trigger",
                 "grant select", "grant update", "service_role", "net.http", "cron."):
        assert word not in sql, word
    assert "security definer set search_path = ''" in sql and "kaibot.is_owner()" in sql
    assert sql.count("update kaibot.ticker_check") == 2  # the only table it writes
    assert "from public, anon;" in sql and "to authenticated;" in sql


# ------------------------------------------------------------------ SQL
def psql(sql: str, uid: str | None = OWNER, check: bool = True):
    pre = f"set test.uid = '{uid or ''}';\n"
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


def q(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, dict):
        return "'" + json.dumps(v).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def ans(sym, status, reason=None, etf=None, name=None, industry=None, detail=None, uid=OWNER, check=True):
    sql = (f"select public.kb_ticker_check_answer({q(sym)}, {q(status)}, {q(reason)}, {q(etf)}, {q(name)}, "
           f"{q(industry)}, {q(detail if detail is not None else {})});")
    r = psql(sql, uid, check=check)
    return json.loads(r.stdout.strip().splitlines()[-1]) if not r.returncode else r.stderr


def row(sym):
    out = psql(f"select to_jsonb(c) from kaibot.ticker_check c where symbol = '{sym}' "
               "and checked_on = (now() at time zone 'America/New_York')::date;", None).stdout.strip()
    return json.loads(out) if out else None


@pytest.fixture
def db():
    psql(f"""delete from kaibot.ticker_check;
             insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
             insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""", None)
    psql("select public.kb_check_ticker('NBIS');")  # today's pending row, as the dashboard leaves it


@needs_pg
def test_owner_only(db):
    for uid in (OTHER, None):
        assert "not authorised" in ans("NBIS", "accepted", etf="SPY", detail=GOOD, uid=uid, check=False)
    assert row("NBIS")["status"] == "pending"
    for role in ("anon", "public"):
        has = psql(f"select has_function_privilege('{role}', 'public.kb_ticker_check_answer(text,text,text,text,"
                   "text,text,jsonb)', 'execute');", None).stdout.strip()
        assert has == "f", role


@needs_pg
def test_accepted_and_refused_answers_are_stored_and_returned(db):
    got = ans("nbis", "accepted", etf="SPY", name="Nebius Group N.V.", detail=GOOD)
    assert got["ok"] is True and got["check"]["status"] == "accepted" and got["check"]["etf"] == "SPY"
    r = row("NBIS")
    assert r["status"] == "accepted" and r["name"] == "Nebius Group N.V." and r["detail"]["sessions"] == 120
    assert json.loads(psql("select public.kb_check_ticker('NBIS');").stdout.strip())["status"] == "accepted"
    psql("select public.kb_check_ticker('ZZZT');")
    why = "We could not find ZZZT at the broker. Check the spelling."
    got = ans("ZZZT", "refused", reason=why, detail={"sessions": 0, "adv20_usd": 0.0, "last_close": None})
    assert got["check"]["status"] == "refused" and got["check"]["reason"] == why and row("ZZZT")["etf"] is None


@needs_pg
def test_an_answer_is_never_overwritten(db):
    ans("NBIS", "accepted", etf="SPY", detail=GOOD)
    again = ans("NBIS", "refused", reason="x")
    assert again["ok"] is False and again["reason"] == "already answered" and again["check"]["status"] == "accepted"
    assert row("NBIS")["status"] == "accepted"
    none = ans("PLTR", "accepted", etf="XLK", detail=GOOD)  # nobody asked for PLTR today: no row is made
    assert none == {"ok": False, "reason": "no check today", "check": None} and row("PLTR") is None
    # yesterday's row is not today's
    psql("insert into kaibot.ticker_check (symbol, checked_on) "
         "values ('UBER', (now() at time zone 'America/New_York')::date - 1);", None)
    assert ans("UBER", "accepted", etf="XLI", detail=GOOD)["reason"] == "no check today"


@needs_pg
def test_input_is_validated(db):
    bad = [
        dict(sym="SPY", status="refused", reason="x"),
        dict(sym="TOOLONG", status="refused", reason="x"),
        dict(sym="NBIS", status="pending"),
        dict(sym="NBIS", status="error", reason="x"),
        dict(sym="NBIS", status="accepted", etf=None, detail=GOOD),
        dict(sym="NBIS", status="accepted", etf="QQQ", detail=GOOD),
        dict(sym="NBIS", status="accepted", etf="SPY", reason="x", detail=GOOD),
        dict(sym="NBIS", status="accepted", etf="SPY", detail={**GOOD, "sessions": 59}),
        dict(sym="NBIS", status="accepted", etf="SPY", detail={**GOOD, "last_close": 4.99}),
        dict(sym="NBIS", status="accepted", etf="SPY", detail={**GOOD, "adv20_usd": 5e7}),
        dict(sym="NBIS", status="accepted", etf="SPY", detail={**GOOD, "adv20_usd": "3e8"}),
        dict(sym="NBIS", status="accepted", etf="SPY", detail={}),
        dict(sym="NBIS", status="refused", reason=""),
        dict(sym="NBIS", status="refused", reason="x" * 301),
        dict(sym="NBIS", status="refused", reason="x", etf="SPY"),
        dict(sym="NBIS", status="refused", reason="x", name="n" * 201),
        dict(sym="NBIS", status="refused", reason="x", detail={"blob": "y" * 5000}),
    ]
    for b in bad:
        sym, status = b.pop("sym"), b.pop("status")
        err = ans(sym, status, check=False, **b)
        assert isinstance(err, str) and "ERROR" in err, (sym, status, b)
    assert row("NBIS")["status"] == "pending" and row("NBIS")["attempts"] == 0


@needs_pg
def test_three_failed_reads_make_an_error_that_can_be_retried(db):
    for n in (1, 2):
        got = ans("NBIS", "failed", detail={"error": "HTTP 500"})
        assert got["ok"] is True and got["check"]["status"] == "pending" and row("NBIS")["attempts"] == n
    got = ans("NBIS", "failed", detail={"error": "HTTP 500"})
    assert got["check"]["status"] == "error"
    # tickercheck.process's words
    assert got["check"]["reason"] == "We could not check NBIS right now. Try again later."
    assert row("NBIS")["detail"] == {"error": "HTTP 500", "checked_by": "vercel"}
    # an errored row still takes an answer; Retry (kb_check_ticker) puts it back to pending
    assert ans("NBIS", "refused", reason="NBIS cannot be traded at the broker right now.")["ok"] is True
    psql("update kaibot.ticker_check set status = 'error', reason = 'x' where symbol = 'NBIS';", None)
    assert json.loads(psql("select public.kb_check_ticker('NBIS');").stdout.strip())["status"] == "pending"
    assert ans("NBIS", "accepted", etf="SPY", detail=GOOD)["check"]["status"] == "accepted"
