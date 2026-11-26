"""One registry of the engine's event names (M10 ticket 1.22, issue #447).

Every name the engine (or a script, an agent, the watchdog) writes to `kaibot.event` is declared here once:
- `HEALTH`: shown on the dashboard's Health page. `kb_health()`'s `type in (...)` list is generated from it
  (`python -m kaibot.engine.event_names --sql`), and so is the page's label map (`--ts`, written to
  apps/web/src/lib/eventNames.ts). The order is the list's order; a new health event goes at the END.
- `PENDING`: health events already written whose kb_health migration is still to come (one free migration number
  at a time, so several PRs share it). `--sql` prints HEALTH + PENDING: the list for that migration; once it has
  landed, move the names from PENDING to the end of HEALTH.
- `ROUTINE`: written and replicated, deliberately not on the Health page (routine flow, not health: a 30-name book
  writes dozens of fills and orders a day, which would push the 80 rows' health events out of the window).

Checks (tests/test_event_names.py): every name the code emits is registered (literal names by a scan of the
source; dynamic ones through their known sources), every event written in the test suite is registered (the test
stores call `note`, strict there), the latest kb_health migration lists exactly HEALTH, and eventNames.ts is the
generator's output. A new event therefore needs one line here, plus, for a health event, a kb_health migration with
the generated list (the stakeholder applies it) and the regenerated eventNames.ts.

Each event has a severity (`SEVERITIES`, M10 1.22 follow-up): `info` (routine or expected, nothing to do), `warn`
(something degraded, skipped or waiting; the engine carries on safely) or `critical` (trading or new buys stopped,
or the books disagree: the owner should look). It is declared here once and generated into eventNames.ts
(`EVENT_SEVERITY`) with the names and the text. No page reads it yet, so the dashboard shows exactly what it showed.

At run time `note` never stops the engine: an unregistered name is written as before and logged once (the event
is the record; losing it would be worse). Audit 2, 5.13 (#693): the first time a name is seen, the store also writes
one visible `unregistered_event` warning (a health event) naming it, so a name the registry lacks shows on the
dashboard instead of only in a log line. Only the tests make it an error (`STRICT`).
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("kaibot.engine")


Severity = Literal["info", "warn", "critical"]
SEVERITIES: tuple[Severity, ...] = ("info", "warn", "critical")


@dataclass(frozen=True)
class Event:
    name: str
    severity: Severity  # how much it matters to the owner (module docstring)
    label: str | None = None  # the Health page's text (None: the page shows the name with spaces)
    why: str = ""  # ROUTINE: why it stays off the Health page


HEALTH: tuple[Event, ...] = (
    # 0007/0021: the engine's start, stop, halts, sessions, commands and the watchdog
    Event("engine_start", "info", "Engine started"),
    Event("engine_stop", "info", "Engine stopped"),
    Event("halt", "critical", "Halt"),
    Event("halt_cleared", "info", "Halt cleared"),
    Event("account_not_clean", "critical", "Paper account not clean"),
    Event("calendar_stale", "warn", "Event calendar out of date"),
    Event("run_paused", "warn", "Run paused"),
    Event("moc_refused", "warn", "Market-on-close refused"),
    Event("watchdog_flatten", "critical", "Watchdog flattened"),
    Event("watchdog_recovered", "info", "Engine heartbeat back"),
    Event("engine_error", "warn", "Engine error (retrying)"),
    Event("shadow_error", "warn", "Shadow strategy error"),
    Event("session_start", "info", "Session started"),
    Event("session_end", "info", "Session closed"),
    Event("shadow_session_end", "info", "Shadow strategies scored"),
    Event("command_pause", "info", None),
    Event("command_resume", "info", None),
    Event("command_flatten_and_stop", "info", None),
    Event("command_set_level", "info", None),
    Event("command_new_run", "info", None),
    # 0021: the live daily path (#206, #207) and its corporate-action steps (#266)
    Event("multi_day_book_ended", "critical", "Experiment ended: the multi-day book reached its stop line"),
    Event("multi_day_retired", "warn", "Experiment ended: rule retired"),
    Event("multi_day_reenabled", "info", "Multi-day rule switched back on by you"),
    Event("multi_day_reenable_refused", "warn", "Switching a rule back on was refused"),
    Event("multi_day_no_decision", "warn", "Multi-day rule: no decision today"),
    Event("multi_day_waiting", "info", "Multi-day rule waiting for the daily prices"),
    Event("multi_day_error", "critical", "Multi-day step failed (no orders sent)"),
    Event("multi_day_overnight_off", "warn", "Overnight holding is off: buys skipped"),
    Event("multi_day_ca_skip", "warn", None),
    Event("multi_day_ca_unread", "warn", None),
    Event("spin_off_adopted", "info", None),
    Event("symbol_change_adopted", "info", None),
    Event("corporate_action_not_adopted", "warn", None),
    # 0029: unfilled orders (#333/#337), the daily note (#311/#318), the pre-open shadow verdicts (#310/#317)
    Event("multi_day_unfilled", "warn", "Order not filled: expired at the close"),
    Event("daily_note", "info", "Daily note written"),
    Event("multi_day_review_shadow", "info", "Pre-open reviewer (observer, no effect on trades)"),
    # 0031: the engine's news poller turned unhealthy / recovered (#354, PR #355)
    Event("news_feed_unhealthy", "warn", "News feed down or empty (stock summaries and the reviewer see no new news)"),
    Event("news_feed_ok", "info", "News feed back"),
    # 0033: engine events the list left out (#392 and a grep of the engine's event names, 20 Nov 2026)
    Event("multi_day_carry", "info", "Unfilled buys carried to the next session, or skipped"),
    Event("corporate_actions_error", "warn", "Couldn't read today's dividends and splits"),
    Event("ledger_mismatch", "critical", "Books differ from the broker"),
    Event("reconcile_blocked", "critical", "Books differ from the broker: new buys stopped"),
    Event("dividend_difference", "warn", "Dividend paid differs from the one expected"),
    Event("venue_fee_unread", "warn", "Couldn't read a trading fee"),
    Event("replica_conflict", "warn", "The dashboard's copy differs from the engine's record (the engine's is kept)"),
    Event("stop_state_fallback", "warn", "Stops shown as missing until a database update is applied"),
    Event("engine_lease_takeover", "warn", "Engine took over from another computer"),
    Event("journal_adopted", "warn", "Engine started from a damaged local record (you allowed it)"),
    Event("order_adopted", "warn", "Order left by a crash taken back"),
    Event("session_finalized_late", "warn", "Session closed late, after a restart"),
    Event("watchdog_protect", "warn", "Watchdog put protective stops back"),
    Event("multi_day_exit_recovered", "warn", "Sale sent before a crash found and booked"),
    Event("multi_day_shorting_on", "warn", "Shorting is on at the broker: multi-day buys held back"),
    # 0036: names sent under a dynamic name (#410's fallback dict, the pre-open review's EVENT, command_{kind})
    Event("multi_day_column_missing", "warn", "Minute prices of multi-day holdings wait for a database update"),
    Event("multi_day_review", "info", "Pre-open reviewer (can veto or halve buys)"),
    Event("command_accept_reconcile", "info", None),
    # 0039: M9 engine safety (tickets 1.12, 1.10, 1.17): the replica, the clock, the hand-kept calendars
    Event("replica_poison", "warn", "A record the dashboard's database refuses was skipped (the engine keeps it)"),
    Event("replica_backlog", "warn", "The dashboard's copy is falling behind the engine's record"),
    Event("clock_skew", "warn", "The engine's clock differs from the broker's: new orders wait until they agree"),
    Event("calendar_expiring", "warn", "A yearly calendar runs out within 30 days and needs extending"),
    Event("command_waiting", "info", "Flatten and stop, or a new run, is waiting until every position is sold"),
    Event("command_rejected", "warn", "A command from the dashboard was refused by the engine"),
    Event("multi_day_book_unchecked", "warn",
          "Today's prices for some holdings can't be read (the last close is used)"),
    Event("multi_day_book_unconfirmed", "warn",
          "Some holdings have had no confirmed price for a while (the last close is used)"),
    Event("multi_day_unbooked_aged", "warn",
          "A sale stopped waiting for an earlier day's trade that never reached the books"),
)

PENDING: tuple[Event, ...] = (
    # Audit 2, 5.13 (#693): an event name the registry lacks was written (once per name and process): a code fault
    Event("unregistered_event", "warn", "The engine wrote an event its registry does not know (tell the team)"),
    # M10 1.24 (#449): a journal snapshot that was not written (memory and a full fold differ) or not used at a start
    Event("journal_snapshot_mismatch", "warn",
          "The engine's start-up snapshot differed from its full record (not used)"),
    Event("journal_snapshot_unused", "info", "The engine started from its full record, not from the snapshot"),
    # M10 1.25 (#450): the engine started reducing only (Supabase down at start), and became fully active again
    Event("engine_degraded_start", "critical",
          "The engine started while its database was unreachable: selling only, no buys"),
    Event("engine_lease_confirmed", "info", "The engine reached its database again and holds the lease"),
    # Audit 2, 5.12 (#692): the daily-bars fetch the engine started itself (the Mac slept through the night job) failed
    Event("daily_fetch_failed", "warn", "The engine's own fetch of the daily prices failed (see logs/fetch_daily.log)"),
    # M12 3.11 (#473): the daily prices missed their deadline; a daily-data check blocks buys, and lets go again
    Event("multi_day_data_late", "warn", "Daily prices not in by their deadline: no multi-day decision until they are"),
    Event("data_entry_block", "warn", "New buys blocked by a daily-data check (sales and stops go on)"),
    Event("data_entry_unblocked", "info", "The daily-data check passes again: buys allowed"),
    # QA-Ops (24-25 Nov 2026 incident, engine/gap.py): the engine was not running for more than an hour while it
    # should have been (no clean stop: a sleep, a power cut, a crash), seen at the next start or on waking
    Event("engine_gap", "warn", "The engine was not running for over an hour (the Mac asleep or off?)"),
    # QA-Ops audit (25 Nov 2026): the calendar unreadable and no local close for the day: sent as on an early close
    Event("multi_day_close_unknown", "warn",
          "Session close unknown: multi-day orders sent only in the early-close window (12:50-12:57 ET)"),
    # Audit 2, 5.9: the venue refused a multi-day sale at every try of the send window (sent again next session)
    Event("multi_day_sale_refused", "critical",
          "The broker refused a sale all through the send window: it is tried again next session"),
    # Audit 2, 5.11: a multi-day decision day whose daily prices never came in time (or that a newer one replaced)
    Event("multi_day_decision_lost", "warn",
          "A multi-day decision was never taken: its daily prices did not come in time"),
    # #746: the broker's calendar unreadable: the session's days and hours from the engine's local NYSE calendar
    Event("calendar_local", "warn",
          "Broker calendar unreadable: the session's days and hours come from the engine's local NYSE calendar"),
)

ROUTINE: tuple[Event, ...] = (
    Event("cash_merger_booked", "info", why="a cash merger booked (S78)"),
    Event("config_applied", "info", why="the owner's config read for a session"),
    Event("custom_tickers", "info", why="the session's custom-ticker check"),
    Event("daily_fetch", "info", why="audit 2 5.12: the engine started, or finished, its own daily-bars fetch"),
    Event("dividend_booked", "info", why="a dividend booked"),
    Event("filing_flag_shadow", "info", why="#375: the 8-K flags' shadow log (research)"),
    Event("headline_feed_failed", "warn",
          why="the dashboard's headline line could not write (display only; nothing stops)"),
    Event("headline_feed_ok", "info", why="the headline line writes again"),
    Event("market_alarm", "info", why="S103: the market-event alarm's notice (gates nothing)"),
    Event("market_alarm_error", "warn", why="S103: its failure row (stops nothing)"),
    Event("multi_day_canceled", "info", why="a daily-path order cancelled"),
    Event("multi_day_decision", "info", why="the evening's decision"),
    Event("multi_day_fill", "info", why="a daily-path fill"),
    Event("multi_day_orders", "info", why="the send's orders"),
    Event("multi_day_plan", "info", why="the send's plan"),
    Event("multi_day_review_applied", "info", why="the pre-open review applied"),
    Event("multi_day_sold", "info", why="a daily-path sale"),
    Event("multi_day_stops_cancelled", "info", why="stops cancelled for a sale"),
    Event("multi_day_view_dropped", "info", why="the daily view dropped stocks"),
    Event("positions_rewritten", "info", why="positions folded again"),
    Event("session_restored", "info", why="a session restored after a restart"),
    Event("split_booked", "info", why="a split booked"),
)

HEALTH_NAMES: tuple[str, ...] = tuple(e.name for e in HEALTH)
NAMES: frozenset[str] = frozenset(HEALTH_NAMES) | {e.name for e in (*PENDING, *ROUTINE)}
STRICT = False  # tests/conftest.py: an unregistered name raises there
_said: set[str] = set()


class UnregisteredEvent(ValueError):
    pass


def note(name: str) -> bool:
    """Called by every store's `event` before it writes. Unregistered: logged once (STRICT: raised). True the first
    time an unregistered name is seen in this process: the store then writes one `unregistered_event` after it."""
    if name in NAMES:
        return False
    if STRICT:
        raise UnregisteredEvent(f"event {name!r} is not in kaibot.engine.event_names: add it (HEALTH or ROUTINE)")
    if name in _said:
        return False
    _said.add(name)
    log.warning("event %r is not registered in engine/event_names.py (written anyway; one unregistered_event)", name)
    return True


def unregistered(name: str) -> dict:
    """The `unregistered_event` warning's payload for an unregistered name."""
    return {"name": str(name)[:120], "action": "add it to src/kaibot/engine/event_names.py (HEALTH or ROUTINE)"}


def _check_registry() -> None:
    events = (*HEALTH, *PENDING, *ROUTINE)
    names = [e.name for e in events]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"event names registered twice: {dup}")
    bad = sorted(e.name for e in events if e.severity not in SEVERITIES)
    if bad:
        raise ValueError(f"events without a severity ({', '.join(SEVERITIES)}): {bad}")


def severity(name: str) -> Severity | None:
    """The registered severity of an event name (None: not registered)."""
    return _SEVERITY.get(name)


_check_registry()
_SEVERITY: dict[str, Severity] = {e.name: e.severity for e in (*HEALTH, *PENDING, *ROUTINE)}


def health_sql(indent: str = "          ") -> str:
    """kb_health's `type in (...)` list for the next kb_health migration: HEALTH, then PENDING."""
    rows, line = [], []
    for n in (*HEALTH_NAMES, *(e.name for e in PENDING)):
        cand = ", ".join([*line, f"'{n}'"])
        if line and len(indent) + len(cand) > 116:
            rows.append(", ".join(line))
            line = []
        line.append(f"'{n}'")
    rows.append(", ".join(line))
    return ",\n".join(indent + r for r in rows)


def labels_ts() -> str:
    """apps/web/src/lib/eventNames.ts: the Health page's labels and the list kb_health returns."""
    lab = {e.name: e.label for e in HEALTH if e.label}  # PENDING joins once kb_health lists it (it moves to HEALTH)
    body = "\n".join(f"  {n}: {json.dumps(t)}," for n, t in lab.items())
    names = ",\n".join(f"  {json.dumps(n)}" for n in HEALTH_NAMES)
    sev = "\n".join(f"  {e.name}: {json.dumps(e.severity)}," for e in (*HEALTH, *PENDING, *ROUTINE))
    kinds = " | ".join(json.dumps(k) for k in SEVERITIES)
    return ("// GENERATED by `PYTHONPATH=src python -m kaibot.engine.event_names --ts` from\n"
            "// src/kaibot/engine/event_names.py (M10 1.22, #447). Do not edit by hand: edit the registry and\n"
            "// regenerate (tests/test_event_names.py fails while this file is stale).\n\n"
            "/** The Health page's text for each health event (kb_health's list). */\n"
            f"export const EVENT_LABELS: Record<string, string> = {{\n{body}\n}};\n\n"
            "/** The event types kb_health() returns, in its order. */\n"
            f"export const HEALTH_EVENTS: readonly string[] = [\n{names},\n];\n\n"
            "/** info: routine, nothing to do. warn: degraded, skipped or waiting; the engine carries on safely.\n"
            " *  critical: trading or new buys stopped, or the books disagree. */\n"
            f"export type EventSeverity = {kinds};\n\n"
            "/** Every registered event's severity (health, pending and routine). */\n"
            f"export const EVENT_SEVERITY: Record<string, EventSeverity> = {{\n{sev}\n}};\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sql", action="store_true", help="kb_health's type list, for a new kb_health migration")
    g.add_argument("--ts", action="store_true", help="apps/web/src/lib/eventNames.ts")
    a = ap.parse_args(argv)
    print(health_sql() if a.sql else labels_ts(), end="" if a.ts else "\n")


if __name__ == "__main__":
    main()
