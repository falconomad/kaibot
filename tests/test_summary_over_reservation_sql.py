"""#324 / migration 0027 on a scratch Postgres (KAIBOT_TEST_PG; CI builds one; skipped otherwise): a stock-summary
call's true cost is recorded even above its reservation, and such a settle is flagged
(kaibot.summary_call.over_reservation) and counted in kb_summary_spend."""

from __future__ import annotations

import json
import os
import shlex
import subprocess

import pytest

PG = os.environ.get("KAIBOT_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0027 applied")
OWNER = "00000000-0000-0000-0000-000000000001"


def psql(sql: str, uid: str | None = OWNER, check: bool = True):
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"],
                       input=f"set test.uid = '{uid or ''}';\n" + sql, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


def js(sql: str, **kw):
    out = psql(f"select {sql};", **kw).stdout.strip().splitlines()
    return json.loads(out[-1]) if out and out[-1] else None


@pytest.fixture(autouse=True)
def setup():
    psql(f"""delete from kaibot.stock_summary; delete from kaibot.summary_call;
      insert into auth.users (id) values ('{OWNER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""", uid=None)


def reserve(sym: str, usd: float) -> int:
    r = js(f"public.kb_summary_reserve('{sym}', 'claude-haiku-5-5', {usd})")
    assert r["ok"], r
    return r["call_id"]


def settle(cid: int, actual: float):
    js(f"public.kb_summary_settle({cid}, {actual}, 900, 120, null, null, null, null, false)")


def row(cid: int) -> list[str]:
    return psql(f"select actual_usd, reserved_usd, over_reservation from kaibot.summary_call where id = {cid};",
                uid=None).stdout.strip().split("|")


def test_a_settle_above_the_reservation_is_recorded_at_its_true_cost_and_flagged():
    cid = reserve("AAPL", 0.001)
    settle(cid, 0.0042)
    actual, reserved, over = row(cid)
    assert float(actual) == pytest.approx(0.0042) and float(reserved) == pytest.approx(0.001) and over == "t"
    s = js("public.kb_summary_spend()")
    assert s["over_reservation_calls"] == 1 and float(s["day_usd"]) == pytest.approx(0.0042)


def test_a_settle_inside_the_reservation_is_not_flagged():
    a, b = reserve("AAPL", 0.001), reserve("MSFT", 0.001)
    settle(a, 0.0003)
    settle(b, 0.001)  # exactly the reservation: honest
    assert row(a)[2] == "f" and row(b)[2] == "f"
    assert js("public.kb_summary_spend()")["over_reservation_calls"] == 0


def test_an_open_call_is_not_flagged_and_the_lifetime_fields_stay():
    cid = reserve("AAPL", 0.001)
    assert row(cid)[2] == "f"
    s = js("public.kb_summary_spend()")
    assert float(s["lifetime_cap"]) == 150.0 and "lifetime_usd" in s and float(s["month_cap"]) == 5.0
