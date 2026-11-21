"""Migration 0033 (kb_health's event list): the engine's health events that 0031's list left out reach the
dashboard through kb_health's `events` (#392 item 4: `corporate_actions_error`; `multi_day_carry` of #366; and the
rest found by a grep of the engine's event names on 20 Nov 2026).

The file checks run everywhere: 0033's function is 0031's, byte for byte, with only the new types added, it keeps
0021's revoke/grant, and the engine still sends every type it adds. The SQL checks need a scratch Postgres with
every migration applied (KAIBOT_TEST_PG; CI and KAIBOT_TEST_PG_AUTO=1 build one; skipped otherwise)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_kb_health_news_events_sql import OTHER, PG, _sql, _types, owner, psql  # noqa: F401 (owner: autouse)

ROOT = Path(__file__).resolve().parents[1]
M31 = ROOT / "migrations" / "0031_kb_health_news_feed_events.sql"
M33 = ROOT / "migrations" / "0033_kb_health_engine_health_events.sql"
NEW = ("multi_day_carry", "corporate_actions_error", "ledger_mismatch", "reconcile_blocked", "dividend_difference",
       "venue_fee_unread", "replica_conflict", "stop_state_fallback", "engine_lease_takeover", "journal_adopted",
       "order_adopted", "session_finalized_late", "watchdog_protect", "multi_day_exit_recovered",
       "multi_day_shorting_on")
# Sent by the engine and left out on purpose: routine flow, not health. They would push the 80 rows' health events
# out of the window (a 30-name book writes dozens of fills and orders a day).
ROUTINE = {"cash_merger_booked", "dividend_booked", "split_booked", "config_applied", "custom_tickers",
           "multi_day_canceled", "multi_day_decision", "multi_day_fill", "multi_day_orders", "multi_day_plan",
           "multi_day_review_applied", "multi_day_sold", "multi_day_stops_cancelled", "multi_day_view_dropped",
           "positions_rewritten", "session_restored"}
# S103 (#377): the market-event alarm's logged notice and its failure row (KAIBOT_MARKET_ALARM=on); it gates
# nothing and a failure stops nothing, so neither is a health event
ROUTINE |= {"market_alarm", "market_alarm_error"}
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0033 applied")
EMIT_RE = re.compile(r'\b(?:event|_event)\(\s*"([a-z_0-9]+)"')


def _emitted() -> set[str]:
    out: set[str] = set()
    for p in [*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        out |= set(EMIT_RE.findall(p.read_text()))
    return out


def test_0033_is_0031_with_only_the_new_types_added():
    old, new = _sql(M31), _sql(M33)
    assert _types(new) == _types(old) + list(NEW)
    head_old, tail_old = old.split("where type in (", 1)[0], old.split("order by ts desc limit 80", 1)[1]
    head_new, tail_new = new.split("where type in (", 1)[0], new.split("order by ts desc limit 80", 1)[1]
    assert (head_new, tail_new) == (head_old, tail_old)
    assert "revoke all on function public.kb_health() from public, anon;" in tail_new
    assert "grant execute on function public.kb_health() to authenticated;" in tail_new


def test_0033_is_the_latest_definition_of_kb_health():
    later = sorted(p.name for p in (ROOT / "migrations").glob("*.sql")
                   if p.name > M33.name and "function public.kb_health()" in p.read_text())
    assert later == []


def test_0033_has_no_destructive_statement():
    s = _sql(M33).lower()
    assert not re.search(r"\b(delete|drop|truncate|alter table|insert|update)\b", s)


def test_the_engine_still_sends_every_type_0033_adds():
    sent = _emitted()
    assert set(NEW) <= sent, sorted(set(NEW) - sent)


def test_every_event_the_engine_sends_is_listed_or_left_out_on_purpose():
    """A new event name must be added to kb_health (a migration) or to ROUTINE here, with its reason."""
    listed = set(_types(_sql(M33)))
    dynamic = {"dividend_booked", "split_booked"}  # f"{a.kind}_booked" in engine/core.py
    unknown = _emitted() - listed - ROUTINE
    assert not unknown, sorted(unknown)
    assert not (ROUTINE - dynamic) & listed


INSERT = """insert into kaibot.event (ts, type, payload, hash) values
  (now() - interval '3 minutes', 'corporate_actions_error', '{"error": "Alpaca 422"}', 't0033-a'),
  (now() - interval '2 minutes', 'multi_day_carry', '{"strategy": "trend_momentum"}', 't0033-b'),
  (now() - interval '1 minute', 'news_feed_ok', '{"state": "ok"}', 't0033-c'),
  (now(), 'multi_day_fill', '{"symbol": "AAPL"}', 't0033-d');"""


@needs_pg
def test_kb_health_returns_the_new_events_and_0031s_and_still_not_the_routine_ones():
    out = psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;").stdout.strip().splitlines()
    got = [e for e in json.loads(out[-1])["events"]
           if e["payload"] and e["type"] in ("corporate_actions_error", "multi_day_carry", "news_feed_ok",
                                             "multi_day_fill")]
    assert [e["type"] for e in got] == ["news_feed_ok", "multi_day_carry", "corporate_actions_error"]


@needs_pg
def test_kb_health_stays_owner_only():
    r = psql(f"begin;\n{INSERT}\nselect public.kb_health();\nrollback;", OTHER, check=False)
    assert r.returncode and "not authorised" in r.stderr


@needs_pg
def test_the_read_only_check_of_migrations_pending_passes():
    out = psql("""select prosrc like '%multi_day_carry%' and prosrc like '%corporate_actions_error%'
                     and prosrc like '%news_feed_ok%' from pg_proc where proname = 'kb_health';""",
               uid=None).stdout.strip()
    assert out == "t"
