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

from kaibot.engine import event_names

ROOT = Path(__file__).resolve().parents[1]
M31 = ROOT / "migrations" / "0031_kb_health_news_feed_events.sql"
M33 = ROOT / "migrations" / "0033_kb_health_engine_health_events.sql"
NEW = ("multi_day_carry", "corporate_actions_error", "ledger_mismatch", "reconcile_blocked", "dividend_difference",
       "venue_fee_unread", "replica_conflict", "stop_state_fallback", "engine_lease_takeover", "journal_adopted",
       "order_adopted", "session_finalized_late", "watchdog_protect", "multi_day_exit_recovered",
       "multi_day_shorting_on")
# Sent by the engine and left out on purpose (routine flow, not health): since M10 1.22 (#447) the one list is
# engine/event_names.ROUTINE, with each name's reason.
ROUTINE = {e.name for e in event_names.ROUTINE}
# Health events whose kb_health migration is still to come: event_names.PENDING (tests/test_event_names.py fails
# while the latest kb_health migration differs from event_names.HEALTH).
AWAITING_KB_HEALTH: set[str] = {e.name for e in event_names.PENDING}
needs_pg = pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0033 applied")
EMIT_RE = re.compile(r'\b(?:event|_event)\(\s*"([a-z_0-9]+)"')
# M9's command_waiting (#427), command_rejected (#440), multi_day_book_unchecked and multi_day_book_unconfirmed
# (#426), and multi_day_unbooked_aged (#507) are health events; they stood in ROUTINE only until a migration
# listed them (#512). 0039 lists them.

# Events sent under a name that is not written at the call (#410's `multi_day_column_missing` was the first one
# missed). Each dynamic call site below, and where its names come from; a new dynamic call fails
# test_every_dynamic_event_call_is_known until it is added here with its source.
CALL_RE = re.compile(r'\b(?:event|_event)\(\s*([^\s"),][^,)]*)')
DYNAMIC_SITES = {
    # engine/__main__.report_conflicts: a replicator fallback's "event" key, "stop_state_fallback" by default
    ("src/kaibot/engine/__main__.py", 'f.get("event"'),
    # engine/__main__.report_news: the news poller's and the headline feed's queued events
    # (`self._events.append(("<name>", ...))` in ingest/news_poll.py and news/feed.py)
    ("src/kaibot/engine/__main__.py", "kind"),
    # live_daily: the agents' `EVENT = "<name>"` constants (pre-open review, its shadow, the daily note)
    ("src/kaibot/engine/live_daily.py", "EVENT"),
    # live_daily's `event=` callback and the watchdog's `_event`: pass-throughs of names written at their callers
    ("src/kaibot/engine/live_daily.py", "kind"),
    ("src/kaibot/engine/watchdog.py", "kind"),
    # engine/core.py: f"command_{kind}" for every command the engine applies, f"{a.kind}_booked" for corporate actions
    ("src/kaibot/engine/core.py", 'f"command_{kind}"'),
    ("src/kaibot/engine/core.py", 'f"{a.kind}_booked"'),
}
FALLBACK_RE = re.compile(r'"event":\s*"([a-z_0-9]+)"')
AGENT_EVENT_RE = re.compile(r'^EVENT\s*=\s*"([a-z_0-9]+)"', re.M)
QUEUED_RE = re.compile(r'_events\.append\(\(\s*"([a-z_0-9]+)"')
COMMAND_RE = re.compile(r'\bkind == (?:"([a-z_]+)"|([A-Z_]+))')


def _sources() -> list[Path]:
    return [*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]


def _dynamic() -> set[str]:
    src = ROOT / "src" / "kaibot"
    out = {"stop_state_fallback"}  # report_conflicts' default
    out |= set(FALLBACK_RE.findall((src / "engine" / "journal.py").read_text()))
    for p in (src / "agents").glob("*.py"):
        out |= set(AGENT_EVENT_RE.findall(p.read_text()))
    out |= set(QUEUED_RE.findall((src / "ingest" / "news_poll.py").read_text()))
    out |= set(QUEUED_RE.findall((src / "news" / "feed.py").read_text()))  # the headline feed, same report_news
    core = (src / "engine" / "core.py").read_text()
    apply = core.split('self.store.event(f"command_{kind}"', 1)[0].rsplit("def ", 1)[1]  # the command loop
    consts = {"ACCEPT_COMMAND": re.search(r'^ACCEPT_COMMAND = "([a-z_]+)"', (src / "engine" / "reconcile.py")
                                          .read_text(), re.M)[1]}
    out |= {f"command_{lit or consts[c]}" for lit, c in COMMAND_RE.findall(apply)}
    out |= {"dividend_booked", "split_booked"}  # f"{a.kind}_booked": the booked kinds (cash mergers have their own)
    return out


def _emitted() -> set[str]:
    out: set[str] = set()
    for p in _sources():
        out |= set(EMIT_RE.findall(p.read_text()))
    return out | _dynamic()


def _latest_kb_health() -> Path:
    return max(p for p in (ROOT / "migrations").glob("*.sql")
               if "create or replace function public.kb_health()" in p.read_text())


def test_0033_is_0031_with_only_the_new_types_added():
    old, new = _sql(M31), _sql(M33)
    assert _types(new) == _types(old) + list(NEW)
    head_old, tail_old = old.split("where type in (", 1)[0], old.split("order by ts desc limit 80", 1)[1]
    head_new, tail_new = new.split("where type in (", 1)[0], new.split("order by ts desc limit 80", 1)[1]
    assert (head_new, tail_new) == (head_old, tail_old)
    assert "revoke all on function public.kb_health() from public, anon;" in tail_new
    assert "grant execute on function public.kb_health() to authenticated;" in tail_new


def test_every_later_definition_of_kb_health_keeps_0033s_types():
    """0036 redefines kb_health after 0033; every later definition must still list 0033's types."""
    later = sorted(p for p in (ROOT / "migrations").glob("*.sql")
                   if p.name > M33.name and "function public.kb_health()" in p.read_text())
    for p in later:
        assert set(NEW) <= set(_types(_sql(p))), p.name


def test_0033_has_no_destructive_statement():
    s = _sql(M33).lower()
    assert not re.search(r"\b(delete|drop|truncate|alter table|insert|update)\b", s)


def test_the_engine_still_sends_every_type_0033_adds():
    sent = _emitted()
    assert set(NEW) <= sent, sorted(set(NEW) - sent)


def test_every_event_the_engine_sends_is_listed_or_left_out_on_purpose():
    """A new event name must be added to kb_health (a migration) or to ROUTINE here, with its reason; a health event
    whose migration is still to come waits in AWAITING_KB_HEALTH."""
    listed = set(_types(_sql(_latest_kb_health())))
    unknown = _emitted() - listed - ROUTINE - AWAITING_KB_HEALTH
    assert not unknown, sorted(unknown)
    assert not ROUTINE & listed
    assert not AWAITING_KB_HEALTH & (listed | ROUTINE), "listed by a migration now: take it out of the waiting set"


def test_every_dynamic_event_call_is_known():
    """A call whose event name is not a literal: its names reach _emitted() only through DYNAMIC_SITES' sources."""
    sites = set()
    for p in _sources():
        text = p.read_text()
        for m in CALL_RE.finditer(text):
            line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
            arg = m.group(1).split(",")[0].strip()
            if line.lstrip().startswith("def ") or line.endswith("on_"):  # definitions; strategies' on_event(ctx)
                continue
            sites.add((str(p.relative_to(ROOT)), arg))
    assert sites - DYNAMIC_SITES == set(), sorted(sites - DYNAMIC_SITES)


def test_the_dynamic_names_are_found():
    """The sources DYNAMIC_SITES name still yield what the engine sends under them (#410, #131, #354, S78)."""
    d = _dynamic()
    assert {"multi_day_column_missing", "multi_day_review", "multi_day_review_shadow", "daily_note",
            "news_feed_unhealthy", "news_feed_ok", "command_pause", "command_set_level", "command_new_run",
            "command_accept_reconcile", "stop_state_fallback", "headline_feed_failed", "headline_feed_ok"} <= d


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
