"""0023 (stock summaries) on a scratch Postgres (KAIBOT_TEST_PG; CI builds one; skipped otherwise): every function is
owner-only, the caps hold ($0.30 a day, $5 a month, $0.02 a call, 2 calls per symbol and day, none once the day's card
exists), unsettled reservations keep counting, and a settled card is read back."""

from __future__ import annotations

import json
import os
import shlex
import subprocess

import pytest

PG = os.environ.get("KAIBOT_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0023 applied")
OWNER, OTHER = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
TODAY = "(now() at time zone 'America/New_York')::date"


def psql(sql: str, uid: str | None = OWNER, check: bool = True):
    r = subprocess.run(["psql", *shlex.split(PG), "-tA", "-q", "-v", "ON_ERROR_STOP=1"],
                       input=f"set test.uid = '{uid or ''}';\n" + sql, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


def call(sql: str, uid: str | None = OWNER):
    out = psql(f"select {sql};", uid).stdout.strip().splitlines()
    return json.loads(out[-1]) if out and out[-1] else None


def reserve(sym="AAPL", usd=0.001):
    return call(f"public.kb_summary_reserve('{sym}', 'claude-haiku-5-5', {usd})")


def settle(cid, actual=0.0003, text="'Apple Inc: Technology.'", ai="true"):
    return call(f"""public.kb_summary_settle({cid}, {actual}, 900, 120, null, {text},
        '{{"about": "Apple Inc: Technology."}}'::jsonb, '[{{"name": "Finnhub profile", "as_of": "2026-11-18"}}]'::jsonb,
        {ai})""")


@pytest.fixture(autouse=True)
def setup():
    psql(f"""delete from kaibot.stock_summary; delete from kaibot.summary_call;
      delete from kaibot.ticker_check where symbol in ('AAPL', 'MSFT');
      insert into auth.users (id) values ('{OWNER}'), ('{OTHER}') on conflict do nothing;
      insert into kaibot.owner (user_id) values ('{OWNER}') on conflict do nothing;""", uid=None)


def test_reserve_settle_and_read_back():
    r = reserve()
    assert r["ok"] is True
    card = settle(r["call_id"])
    assert card["symbol"] == "AAPL" and card["today"] is True and card["ai"] is True
    assert card["sources"][0]["name"] == "Finnhub profile"
    assert call("public.kb_stock_summary('aapl')")["text"] == "Apple Inc: Technology."
    assert reserve() == {"ok": False, "reason": "cached"}  # one card per symbol and day
    s = call("public.kb_summary_spend()")
    assert float(s["day_usd"]) == pytest.approx(0.0003) and s["calls_today"] == 1 and s["cards_today"] == 1


def test_a_refused_draft_is_stored_as_plain_facts_and_a_call_settles_once():
    r = reserve()
    assert settle(r["call_id"], ai="false")["ai"] is False
    assert psql(f"select public.kb_summary_settle({r['call_id']}, 0, 0, 0, null, null, null, null, false);",
                check=False).returncode


def test_a_failed_call_counts_and_two_calls_a_symbol_a_day_at_most():
    for _ in range(2):
        r = reserve()
        assert r["ok"]
        assert settle(r["call_id"], actual=0.0002, text="null") is None
    assert reserve() == {"ok": False, "reason": "symbol_limit"}


def test_an_unsettled_call_blocks_a_second_one_and_keeps_counting():
    assert reserve(usd=0.02)["ok"]
    assert reserve() == {"ok": False, "reason": "busy"}
    assert float(call("public.kb_summary_spend()")["day_usd"]) == pytest.approx(0.02)


def test_the_day_and_month_caps():
    psql(f"""insert into kaibot.summary_call (day, symbol, model, reserved_usd, actual_usd, settled_at)
             select {TODAY}, 'XOM', 'm', 0.02, 0.02, now() from generate_series(1, 14);""", uid=None)  # $0.28
    assert reserve(usd=0.02)["ok"] is True                                                         # $0.30 exactly
    assert reserve("MSFT", 0.001) == {"ok": False, "reason": "day_cap"}
    psql(f"""delete from kaibot.summary_call;
             insert into kaibot.summary_call (day, symbol, model, reserved_usd, actual_usd, settled_at)
             select {TODAY} - 1, 'XOM', 'm', 0.02, 0.02, now() from generate_series(1, 250)
             where date_trunc('month', {TODAY} - 1) = date_trunc('month', {TODAY});""", uid=None)
    if call("public.kb_summary_spend()")["month_usd"] >= 5:  # (on the 1st of a month yesterday is last month)
        assert reserve("MSFT", 0.001) == {"ok": False, "reason": "month_cap"}
    assert reserve("MSFT", 0.03) == {"ok": False, "reason": "too_large"}


def test_facts_come_from_news_items_and_the_ticker_check():
    psql(f"""insert into kaibot.ticker_check (symbol, checked_on, status, etf, name, industry, detail)
             values ('AAPL', {TODAY}, 'accepted', 'XLK', 'Apple Inc', 'Technology', '{{"exchange": "NASDAQ"}}');
             insert into kaibot.news_item (source, published_at, available_ts, headline, tickers, headline_sha1) values
               ('benzinga', now() - interval '1 day', now() - interval '1 day', 'Apple raises dividend',
                '{{AAPL}}', 'a'),
               ('benzinga', now() - interval '9 days', now() - interval '9 days', 'Too old', '{{AAPL}}', 'b'),
               ('benzinga', now() - interval '1 hour', now() - interval '1 hour', 'Other', '{{MSFT}}', 'c');""",
         uid=None)
    f = call("public.kb_summary_facts('AAPL')")
    assert f["profile"]["name"] == "Apple Inc" and f["profile"]["exchange"] == "NASDAQ"
    assert [n["headline"] for n in f["news"]] == ["Apple raises dividend"]
    psql("delete from kaibot.news_item where headline_sha1 in ('a', 'b', 'c');", uid=None)


@pytest.mark.parametrize("fn", ["public.kb_stock_summary('AAPL')", "public.kb_summary_facts('AAPL')",
                                "public.kb_summary_reserve('AAPL', 'm', 0.001)", "public.kb_summary_spend()",
                                "public.kb_summary_settle(1, 0, 0, 0, null, null, null, null, false)"])
def test_everything_is_owner_only(fn):
    r = psql(f"select {fn};", uid=OTHER, check=False)
    assert r.returncode and "not authorised" in r.stderr
