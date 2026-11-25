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

At run time `note` never stops the engine: an unregistered name is written as before and logged once (the event
is the record; losing it would be worse). Only the tests make it an error (`STRICT`).
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass

log = logging.getLogger("kaibot.engine")


@dataclass(frozen=True)
class Event:
    name: str
    label: str | None = None  # the Health page's text (None: the page shows the name with spaces)
    why: str = ""  # ROUTINE: why it stays off the Health page


HEALTH: tuple[Event, ...] = (
    # 0007/0021: the engine's start, stop, halts, sessions, commands and the watchdog
    Event("engine_start", "Engine started"),
    Event("engine_stop", "Engine stopped"),
    Event("halt", "Halt"),
    Event("halt_cleared", "Halt cleared"),
    Event("account_not_clean", "Paper account not clean"),
    Event("calendar_stale", "Event calendar out of date"),
    Event("run_paused", "Run paused"),
    Event("moc_refused", "Market-on-close refused"),
    Event("watchdog_flatten", "Watchdog flattened"),
    Event("watchdog_recovered", "Engine heartbeat back"),
    Event("engine_error", "Engine error (retrying)"),
    Event("shadow_error", "Shadow strategy error"),
    Event("session_start", "Session started"),
    Event("session_end", "Session closed"),
    Event("shadow_session_end", "Shadow strategies scored"),
    Event("command_pause", None),
    Event("command_resume", None),
    Event("command_flatten_and_stop", None),
    Event("command_set_level", None),
    Event("command_new_run", None),
    # 0021: the live daily path (#206, #207) and its corporate-action steps (#266)
    Event("multi_day_book_ended", "Experiment ended: the multi-day book reached its stop line"),
    Event("multi_day_retired", "Experiment ended: rule retired"),
    Event("multi_day_reenabled", "Multi-day rule switched back on by you"),
    Event("multi_day_reenable_refused", "Switching a rule back on was refused"),
    Event("multi_day_no_decision", "Multi-day rule: no decision today"),
    Event("multi_day_waiting", "Multi-day rule waiting for the daily prices"),
    Event("multi_day_error", "Multi-day step failed (no orders sent)"),
    Event("multi_day_overnight_off", "Overnight holding is off: buys skipped"),
    Event("multi_day_ca_skip", None),
    Event("multi_day_ca_unread", None),
    Event("spin_off_adopted", None),
    Event("symbol_change_adopted", None),
    Event("corporate_action_not_adopted", None),
    # 0029: unfilled orders (#333/#337), the daily note (#311/#318), the pre-open shadow verdicts (#310/#317)
    Event("multi_day_unfilled", "Order not filled: expired at the close"),
    Event("daily_note", "Daily note written"),
    Event("multi_day_review_shadow", "Pre-open reviewer (observer, no effect on trades)"),
    # 0031: the engine's news poller turned unhealthy / recovered (#354, PR #355)
    Event("news_feed_unhealthy", "News feed down or empty (stock summaries and the reviewer see no new news)"),
    Event("news_feed_ok", "News feed back"),
    # 0033: engine events the list left out (#392 and a grep of the engine's event names, 20 Nov 2026)
    Event("multi_day_carry", "Unfilled buys carried to the next session, or skipped"),
    Event("corporate_actions_error", "Couldn't read today's dividends and splits"),
    Event("ledger_mismatch", "Books differ from the broker"),
    Event("reconcile_blocked", "Books differ from the broker: new buys stopped"),
    Event("dividend_difference", "Dividend paid differs from the one expected"),
    Event("venue_fee_unread", "Couldn't read a trading fee"),
    Event("replica_conflict", "The dashboard's copy differs from the engine's record (the engine's is kept)"),
    Event("stop_state_fallback", "Stops shown as missing until a database update is applied"),
    Event("engine_lease_takeover", "Engine took over from another computer"),
    Event("journal_adopted", "Engine started from a damaged local record (you allowed it)"),
    Event("order_adopted", "Order left by a crash taken back"),
    Event("session_finalized_late", "Session closed late, after a restart"),
    Event("watchdog_protect", "Watchdog put protective stops back"),
    Event("multi_day_exit_recovered", "Sale sent before a crash found and booked"),
    Event("multi_day_shorting_on", "Shorting is on at the broker: multi-day buys held back"),
    # 0036: names sent under a dynamic name (#410's fallback dict, the pre-open review's EVENT, command_{kind})
    Event("multi_day_column_missing", "Minute prices of multi-day holdings wait for a database update"),
    Event("multi_day_review", "Pre-open reviewer (can veto or halve buys)"),
    Event("command_accept_reconcile", None),
    # 0039: M9 engine safety (tickets 1.12, 1.10, 1.17): the replica, the clock, the hand-kept calendars
    Event("replica_poison", "A record the dashboard's database refuses was skipped (the engine keeps it)"),
    Event("replica_backlog", "The dashboard's copy is falling behind the engine's record"),
    Event("clock_skew", "The engine's clock differs from the broker's: new orders wait until they agree"),
    Event("calendar_expiring", "A yearly calendar runs out within 30 days and needs extending"),
    Event("command_waiting", "Flatten and stop, or a new run, is waiting until every position is sold"),
    Event("command_rejected", "A command from the dashboard was refused by the engine"),
    Event("multi_day_book_unchecked", "Today's prices for some holdings can't be read (the last close is used)"),
    Event("multi_day_book_unconfirmed",
          "Some holdings have had no confirmed price for a while (the last close is used)"),
    Event("multi_day_unbooked_aged", "A sale stopped waiting for an earlier day's trade that never reached the books"),
)

PENDING: tuple[Event, ...] = (
    # M10 1.24 (#449): a journal snapshot that was not written (memory and a full fold differ) or not used at a start
    Event("journal_snapshot_mismatch", "The engine's start-up snapshot differed from its full record (not used)"),
    Event("journal_snapshot_unused", "The engine started from its full record, not from the snapshot"),
    # M10 1.25 (#450): the engine started reducing only (Supabase down at start), and became fully active again
    Event("engine_degraded_start", "The engine started while its database was unreachable: selling only, no buys"),
    Event("engine_lease_confirmed", "The engine reached its database again and holds the lease"),
    # M12 3.11 (#473): the daily prices missed their deadline; a daily-data check blocks buys, and lets go again
    Event("multi_day_data_late", "Daily prices not in by their deadline: no multi-day decision until they are"),
    Event("data_entry_block", "New buys blocked by a daily-data check (sales and stops go on)"),
    Event("data_entry_unblocked", "The daily-data check passes again: buys allowed"),
)

ROUTINE: tuple[Event, ...] = (
    Event("cash_merger_booked", why="a cash merger booked (S78)"),
    Event("config_applied", why="the owner's config read for a session"),
    Event("custom_tickers", why="the session's custom-ticker check"),
    Event("dividend_booked", why="a dividend booked"),
    Event("filing_flag_shadow", why="#375: the 8-K flags' shadow log (research)"),
    Event("headline_feed_failed", why="the dashboard's headline line could not write (display only; nothing stops)"),
    Event("headline_feed_ok", why="the headline line writes again"),
    Event("market_alarm", why="S103: the market-event alarm's notice (gates nothing)"),
    Event("market_alarm_error", why="S103: its failure row (stops nothing)"),
    Event("multi_day_canceled", why="a daily-path order cancelled"),
    Event("multi_day_decision", why="the evening's decision"),
    Event("multi_day_fill", why="a daily-path fill"),
    Event("multi_day_orders", why="the send's orders"),
    Event("multi_day_plan", why="the send's plan"),
    Event("multi_day_review_applied", why="the pre-open review applied"),
    Event("multi_day_sold", why="a daily-path sale"),
    Event("multi_day_stops_cancelled", why="stops cancelled for a sale"),
    Event("multi_day_view_dropped", why="the daily view dropped stocks"),
    Event("positions_rewritten", why="positions folded again"),
    Event("session_restored", why="a session restored after a restart"),
    Event("split_booked", why="a split booked"),
)

HEALTH_NAMES: tuple[str, ...] = tuple(e.name for e in HEALTH)
NAMES: frozenset[str] = frozenset(HEALTH_NAMES) | {e.name for e in (*PENDING, *ROUTINE)}
STRICT = False  # tests/conftest.py: an unregistered name raises there
_said: set[str] = set()


class UnregisteredEvent(ValueError):
    pass


def note(name: str) -> None:
    """Called by every store's `event` before it writes. Unregistered: logged once (STRICT: raised)."""
    if name in NAMES:
        return
    if STRICT:
        raise UnregisteredEvent(f"event {name!r} is not in kaibot.engine.event_names: add it (HEALTH or ROUTINE)")
    if name not in _said:
        _said.add(name)
        log.warning("event %r is not registered in engine/event_names.py (written anyway)", name)


def _check_registry() -> None:
    names = [e.name for e in (*HEALTH, *PENDING, *ROUTINE)]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"event names registered twice: {dup}")


_check_registry()


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
    return ("// GENERATED by `PYTHONPATH=src python -m kaibot.engine.event_names --ts` from\n"
            "// src/kaibot/engine/event_names.py (M10 1.22, #447). Do not edit by hand: edit the registry and\n"
            "// regenerate (tests/test_event_names.py fails while this file is stale).\n\n"
            "/** The Health page's text for each health event (kb_health's list). */\n"
            f"export const EVENT_LABELS: Record<string, string> = {{\n{body}\n}};\n\n"
            "/** The event types kb_health() returns, in its order. */\n"
            f"export const HEALTH_EVENTS: readonly string[] = [\n{names},\n];\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sql", action="store_true", help="kb_health's type list, for a new kb_health migration")
    g.add_argument("--ts", action="store_true", help="apps/web/src/lib/eventNames.ts")
    a = ap.parse_args(argv)
    print(health_sql() if a.sql else labels_ts(), end="" if a.ts else "\n")


if __name__ == "__main__":
    main()
