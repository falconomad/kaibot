"""#319 / migration 0026 on a scratch Postgres (KAIBOT_TEST_PG; CI builds one; skipped otherwise): one $150 lifetime
cap across the engine's LLM ledger (kaibot.budget_day, open kaibot.llm_call reservations) and the stock summaries
(kaibot.summary_call at coalesce(actual_usd, reserved_usd)). Both reservation paths read the same total under the
same row lock (today's budget_day row), so together they cannot pass it."""

from __future__ import annotations

import json
import os
import shlex
import subprocess

import pytest

PG = os.environ.get("KAIBOT_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0026 applied")
OWNER = "00000000-0000-0000-0000-000000000001"
TODAY = "(now() at time zone 'America/New_York')::date"


def psql(sql: str, uid: str | None = OWNER, role: str | None = None, check: bool = True):
    pre = f"set test.uid = '{uid or ''}';\n" + (f"set role {role};\n" if role else "")
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"], input=pre + sql,
                       capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


def one(sql: str, **kw):
    out = psql(f"select {sql};", **kw).stdout.strip().splitlines()
    return out[-1] if out else None


def js(sql: str, **kw):
    v = one(sql, **kw)
    return json.loads(v) if v else None


def summary(usd: float, settled: bool = True, day: str = f"{TODAY} - 40", symbol: str = "XOM"):
    psql(f"""insert into kaibot.summary_call (day, symbol, model, reserved_usd, actual_usd, settled_at)
             values ({day}, '{symbol}', 'm', 0.02, {usd if settled else 'null'}, {'now()' if settled else 'null'});""",
         uid=None)


def reserve_llm(usd=0.05, lifetime=150.0):
    return one(f"kaibot.llm_reserve_lifetime('claude-haiku-5-5', 't', {usd}, 1.0, 25.0, {lifetime}, 300)", uid=None)


def reserve_summary(sym="AAPL", usd=0.001):
    return js(f"public.kb_summary_reserve('{sym}', 'claude-haiku-5-5', {usd})")


@pytest.fixture(autouse=True)
def setup():
    psql(f"""delete from kaibot.stock_summary; delete from kaibot.summary_call; delete from kaibot.llm_call;
      delete from kaibot.budget_day;
      insert into auth.users (id) values ('{OWNER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""", uid=None)


def spent(llm_before: float = 0.0, llm_month: float = 0.0):
    psql(f"""insert into kaibot.budget_day (day, spent_usd) values
             (date_trunc('month', {TODAY})::date - 40, {llm_before}), ({TODAY}, {llm_month})
             on conflict (day) do update set spent_usd = excluded.spent_usd;""", uid=None)


def test_ai_spend_adds_both_ledgers_and_open_reservations():
    spent(100.0, 0.5)
    for _ in range(3):
        summary(0.01)
    summary(0, settled=False)  # an open summary counts at its reservation ($0.02)
    assert reserve_llm(0.05) is not None  # an open engine reservation counts too
    s = js("kaibot.ai_spend()", uid=None)
    assert float(s["llm"]) == pytest.approx(100.5) and float(s["llm_before_month"]) == pytest.approx(100.0)
    assert float(s["summaries"]) == pytest.approx(0.05) and float(s["llm_open"]) == pytest.approx(0.05)
    assert float(s["total"]) == pytest.approx(100.6) and float(s["cap"]) == 150.0


def test_the_engine_reservation_counts_the_summaries():
    spent(140.0, 0.0)
    for _ in range(499):
        summary(0.02)  # $9.98 of summaries: $0.02 left in all
    assert reserve_llm(0.05) in (None, "")  # refused although the day and the month are free
    assert one("count(*) from kaibot.llm_call", uid=None) == "0"
    assert one(f"state from kaibot.budget_day where day = {TODAY}", uid=None) == "no_entries"
    assert reserve_llm(0.01) not in (None, "")  # fits the $0.02 left


def test_the_lifetime_cap_can_only_tighten():
    spent(149.0, 0.0)
    assert reserve_llm(0.05, lifetime=1000.0) not in (None, "")  # the SQL's own $150 holds
    psql("delete from kaibot.llm_call;", uid=None)
    spent(149.99, 0.0)
    assert reserve_llm(0.05, lifetime=1000.0) in (None, "")
    spent(10.0, 0.0)
    assert reserve_llm(0.05, lifetime=10.0) in (None, "")  # a tighter cap from the engine's config is kept


def test_the_summary_reservation_counts_the_engines_spend():
    spent(149.0, 0.995)  # $149.995 of LLM spend
    assert reserve_summary(usd=0.01) == {"ok": False, "reason": "lifetime_cap"}
    assert reserve_summary(usd=0.005)["ok"] is True  # $150.000 exactly
    spent(0.0, 0.0)
    assert reserve_llm(0.06) not in (None, "")  # an open engine reservation counts for the summaries as well
    psql(f"update kaibot.budget_day set spent_usd = 149.94 where day = {TODAY};", uid=None)
    assert reserve_summary("MSFT", 0.001) == {"ok": False, "reason": "lifetime_cap"}


def test_the_other_caps_of_0023_still_hold():
    r = reserve_summary()
    assert r["ok"] is True
    assert reserve_summary() == {"ok": False, "reason": "busy"}
    assert reserve_summary("MSFT", 0.03) == {"ok": False, "reason": "too_large"}


def test_both_dashboard_functions_show_the_combined_total():
    spent(100.0, 1.0)
    summary(0.5)
    b = js("public.kb_llm_budget()")
    assert float(b["lifetime_usd"]) == pytest.approx(101.5) and float(b["lifetime_summaries_usd"]) == 0.5
    assert float(b["caps"]["lifetime_hard"]) == 150.0
    s = js("public.kb_summary_spend()")
    assert float(s["lifetime_usd"]) == pytest.approx(101.5) and float(s["lifetime_cap"]) == 150.0
    assert float(s["day_cap"]) == 0.30 and float(s["month_cap"]) == 5.00  # 0023's fields unchanged


@pytest.mark.parametrize("fn", ["kaibot.ai_spend()",
                                "kaibot.llm_reserve_lifetime('m', 't', 0.01, 1.0, 25.0, 150.0, 300)"])
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_the_engine_functions_are_service_role_only(fn, role):
    r = psql(f"select {fn};", uid=None, role=role, check=False)
    assert r.returncode and "permission denied" in r.stderr
    assert psql(f"select {fn};", uid=None, role="service_role", check=False).returncode == 0


@pytest.mark.parametrize("call", ["engine", "summary"])
def test_both_reservations_wait_for_the_one_ai_lock_on_any_day(call):
    """QA-Ops on #322: today's budget_day row is a different row on either side of midnight ET, so both paths also
    take one advisory lock that does not depend on the day. While another session holds it, neither reserves."""
    import time

    holder = subprocess.Popen(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    holder.stdin.write("begin; select pg_advisory_xact_lock(hashtext('kaibot.ai_lifetime')); select 'held';\n")
    holder.stdin.flush()
    try:
        while (line := holder.stdout.readline()) and line.strip() != "held":  # the lock's void row comes first
            pass
        assert line.strip() == "held", holder.stderr.read()
        sql = ("select kaibot.llm_reserve_lifetime('m', 't', 0.01, 1.0, 25.0, 150.0, 300);" if call == "engine" else
               "select public.kb_summary_reserve('AAPL', 'm', 0.001);")
        t0 = time.monotonic()
        r = psql(f"set lock_timeout = '700ms';\n{sql}", uid=None if call == "engine" else OWNER, check=False)
        assert r.returncode and "lock timeout" in r.stderr and time.monotonic() - t0 >= 0.6
        assert one("count(*) from kaibot.llm_call", uid=None) == "0"
        assert one("count(*) from kaibot.summary_call", uid=None) == "0"
    finally:
        holder.stdin.write("commit;\n")
        holder.stdin.close()
        holder.wait(timeout=10)
    assert psql(sql, uid=None if call == "engine" else OWNER, check=False).returncode == 0  # free again: it reserves
