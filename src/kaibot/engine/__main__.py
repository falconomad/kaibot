"""Run the engine: `python -m kaibot.engine` (or the Docker image). Settings come from the environment / .env.

Loop: every ~10 s apply dashboard commands and send a heartbeat; once per completed minute (at :07) run a tick.
Stops cleanly on Ctrl-C / SIGTERM. Open positions keep their broker-held stops either way.

Before anything touches the broker the engine takes the cross-machine lease (issue #81, engine/lease.py): if
another engine holds it, or it cannot be checked, this process exits with code 3 and trades nothing.

S77 (#117): the engine's state lives in the local journal `<KAIBOT_DATA_DIR>/journal.sqlite` (engine/journal.py),
written before every broker call; Supabase is a replica fed by a background thread. The first start of a new
journal copies the active run from Supabase and exits with code 4 if Supabase cannot be read.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import socket
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import yaml

from kaibot.agents.gateway import from_env as gateway_from_env
from kaibot.common import http
from kaibot.engine import fetch as engine_fetch
from kaibot.engine.alpaca import AlpacaData, AlpacaPaper
from kaibot.engine.book_market import BookMarket
from kaibot.engine.config import ROOT, Settings
from kaibot.engine.core import CONFIG_UNREADABLE, RULES_RETRY_S, DailyPathMissing, Engine
from kaibot.engine.corp_guard import RecentActions
from kaibot.engine.dividends import ExDateDividends
from kaibot.engine.events import load_calendar
from kaibot.engine.fence import FencedBroker
from kaibot.engine.gap import GapWatch
from kaibot.engine.journal import (
    BootstrapError,
    DurabilityError,
    IdentityError,
    IdentityUnreadable,
    Journal,
    JournalCorrupt,
    Replicator,
    SnapshotMismatch,
    journal_path,
    live_store,
)
from kaibot.engine.lease import (
    EXIT_LEASE,
    EngineLease,
    LeaseError,
    LeaseHeld,
    LeaseLost,
    LeaseUnavailable,
    holder_id,
)
from kaibot.engine.m3glue import (
    LockedDesk,
    make_daily_note,
    make_pack_fn,
    make_preopen,
    make_preopen_shadow,
    make_rules_extra,
)
from kaibot.engine.store import Store
from kaibot.ingest import news_poll
from kaibot.news import NewsDesk
from kaibot.news import configure as configure_news
from kaibot.risk.profiles import intraday_enabled, load_profiles, load_strategies

log = logging.getLogger("kaibot.engine")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def vix_loader(fred_key: str):
    def load() -> pd.Series:
        params = {
            "series_id": "VIXCLS",
            "observation_start": str((pd.Timestamp.now() - pd.Timedelta(days=60)).date()),
            "file_type": "json",
            "api_key": fred_key,
        }
        obs = http.get_json(FRED_URL, source="fred", params=params, timeout=30)["observations"]
        s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs if o["value"] != "."}).sort_index()
        return (
            s.shift(1, freq="D")
            .resample("D")
            .ffill()
            .reindex(pd.date_range(s.index.min(), pd.Timestamp.now().normalize() + pd.Timedelta(days=1)))
            .ffill()
        )

    return load


def refuse_unchecked_block(market) -> None:
    """M12 3.11 (#473), Product's ruling: KAIBOT_DATA_CHECKS_BLOCK on, and this engine's market has no data checks
    (the multi-day book's loop alone reads the minute bars of its holdings only, engine/book_market.py): the switch
    could block nothing, so the engine does not start (fail closed) rather than run believing it is on."""
    from kaibot.common import switches  # not engine/market.py: the book's loop imports no intraday module

    checks = switches.value("KAIBOT_DATA_CHECKS_BLOCK")
    if checks and getattr(market, "data_block", None) is None:
        raise SystemExit(
            f"KAIBOT_DATA_CHECKS_BLOCK is on ({', '.join(sorted(checks))}), but this engine's market has no data "
            "checks: the intraday path is off (config/profiles.yaml intraday.enabled), so the engine reads only the "
            "minute bars of its holdings and could block nothing. Not starting. Set KAIBOT_DATA_CHECKS_BLOCK=off, or "
            "turn the intraday path on (docs/ops/failsafe.md, 'market data checks').")


def build(settings: Settings) -> Engine:
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    etf_of = {s: etf for etf, syms in cfg["sectors"].items() for s in syms}
    # M13 4.3 (#477): every order call behind the lease's fence (engine/fence.py; the lease is set at start-up)
    broker = FencedBroker(AlpacaPaper(settings.alpaca_key, settings.alpaca_secret))
    data = AlpacaData(settings.alpaca_key, settings.alpaca_secret)
    strategies = load_strategies()
    # M10 1.19 (#444): the intraday path (its market, the agent room, the shadow rules) is built only when a level
    # runs an intraday rule: `intraday.enabled: true` in config/profiles.yaml. Off, the engine is the multi-day
    # book's loop alone and reads the minute bars of its holdings only (engine/book_market.py).
    intraday = intraday_enabled()
    if intraday:
        from kaibot.engine.market import LiveMarket

        market = LiveMarket(
            data, sorted(etf_of), etf_of, cfg["market"], settings.data_dir / "history",
            vix_loader(settings.fred_key),
            minute_data=bounded_data(data),  # M9 1.5: the per-minute bars read ends within 20 s
        )
    else:
        market = BookMarket(data, {*etf_of, *etf_of.values()}, cfg["market"], minute_data=bounded_data(data))
    log.info("intraday path: %s", "ON (config/profiles.yaml intraday.enabled)" if intraday
             else "off (the multi-day book's loop only)")
    refuse_unchecked_block(market)  # M12 3.11 (#473), Product: fail closed before anything else is built
    remote = Store(settings.supabase_url, settings.supabase_service_key)
    # S77: the engine's own tables live in the local journal, written before any broker call; Supabase is a replica
    # M10 1.24 (#449): the minute rows are not kept in memory, and the fold starts at the newest snapshot
    store = live_store(open_journal(journal_path(settings.data_dir)), remote)
    events, covers_until = load_calendar(ROOT / "config" / "macro_calendar.yaml")
    fomc = {np.datetime64(d, "D") for d, evs in events.items() if any(e == "fomc_statement_1400" for e, _ in evs)}
    gw = gateway_from_env(remote, os.environ)  # S32/S34: the room runs only with ANTHROPIC_API_KEY and KAIBOT_ROOM=on
    room = None
    if intraday and gw.enabled:  # the room reviews intraday candidates only
        from kaibot.agents.room import Room

        room = Room(gw)
    log.info("agent room: %s", "ON (Sonnet, capped budget)" if room else "off (rules-only)")
    # S33: one news desk per engine; it triages through the gateway only when the room is on.
    desk = NewsDesk(remote, source=data, gateway=gw if gw.enabled else None)
    try:
        log.info("news window: %d items reloaded", desk.load_window())
    except Exception:
        log.exception("news window reload failed: starting empty")
    configure_news(desk)
    locked = LockedDesk(desk)
    eng = Engine(broker, market, store, load_profiles(), fomc, process=settings.engine_name,
                  calendar_until=covers_until, events=events, strategies=strategies, room=room,
                  pack_fn=make_pack_fn(locked) if intraday else None,
                  rules_extra=make_rules_extra(locked) if intraday else None,
                  ex_dividends=ExDateDividends(data),  # S78: dividends accrued at the ex-date (#118)
                  daily=daily_path(data, review=make_preopen(gw, locked),  # #131: review None unless switched on
                                   shadow=make_preopen_shadow(remote, locked),  # #310: None unless shadow
                                   note=make_daily_note(remote)),  # #311: None unless KAIBOT_DAILY_NOTE=on
                  # #266: spin-offs and symbol changes of held multi-day positions, adopted from Alpaca's record
                  # (only used with the daily path: KAIBOT_MULTIDAY=on)
                  recent_actions=RecentActions(get=bounded_data_get(data)),
                  # S103 (#377): the market-event alarm as a logged notice; off unless KAIBOT_MARKET_ALARM=on
                  market_alarm=market_alarm_from_env(os.environ),
                  intraday=intraday)  # M10 1.19: False builds the book's engine; no intraday module is imported
    # Data (19 Nov 2026): news on a schedule, so news_item fills without the room (ingest/news_poll.py). Started in
    # main() after the lease; never with --check. KAIBOT_NEWS_POLL=off: None
    eng.news_poller = news_poll.for_engine(eng, desk, data, locked.lock)
    eng.headline_feed = headline_feed(eng, remote, desk, eng.news_poller)
    if not intraday:
        market.holdings = eng._md_symbols  # the book's sleeves and the symbols of its working orders
    return eng


def headline_feed(eng: Engine, remote, desk, poller, env=None):
    """The dashboard's headline line (news/feed.py): the headlines the poller and the desk fetch anyway, copied to
    kaibot.headline on the feed's own thread; SEC 8-K lines too with KAIBOT_HEADLINE_EDGAR=on. Display only: nothing
    of it is read back. Started in go_active (the lease holder only); None with KAIBOT_HEADLINE_FEED=off."""
    try:
        from kaibot.news import feed  # display only; loaded only here (tests/unit/test_news_tags.py)

        def focus():
            sc = news_poll.engine_scope(eng)
            return sc.planned | sc.held

        f = feed.for_engine(remote, focus, os.environ if env is None else env)
    except Exception:
        log.exception("headline feed not built: the dashboard shows no new headlines")
        return None
    if f is not None:
        desk.feed = f
        if poller is not None:
            poller.feed = f
    return f


def bounded_data(data):
    """M9 1.5 (audit M3): the market-data client for reads made inside the engine's loop every minute
    (`LiveMarket.update`), bounded like an order call: 6 s an attempt, 20 s in all, retries included. A copy with
    the same keys and the same rate budget. The plain client (30 s an attempt, five attempts, no deadline) could
    hold a tick for minutes, past the 15:50-15:57 ET send window and the watchdog's 90 s."""
    import copy

    from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S

    quick = copy.copy(data)
    quick.http = copy.copy(data.http)
    quick.http.timeout_s, quick.http.deadline_s = ORDER_TIMEOUT_S, ORDER_DEADLINE_S
    return quick


def bounded_data_get(data):
    """QA-Ops (#271, #275): Alpaca market-data GETs made inside the engine's loop (the corporate-actions reads of
    the send step and of the session start), bounded like an order call: 6 s an attempt, 20 s in all, retries
    included. Same client, same keys in its headers, same rate budget. The plain client (30 s an attempt, five
    attempts, no deadline) could hold the loop for minutes, past the watchdog's 90 s."""
    import copy

    from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S
    from kaibot.engine.config import DATA_URL

    http = copy.copy(data.http)
    http.timeout_s, http.deadline_s = ORDER_TIMEOUT_S, ORDER_DEADLINE_S
    return lambda path, params: http.call("GET", f"{DATA_URL}{path}", params)


def _switch(name: str, env=None):
    """M10 1.23 (#448): an engine switch, parsed by kaibot.common.switches (imported here, not at the top, to stay
    clear of the import block the book-loop rewrite #538 changes)."""
    from kaibot.common import switches

    return switches.get(name, env)


def market_alarm_from_env(env) -> object | None:
    """S103 (#377): the market-event alarm's (session, now) -> flags, or None unless KAIBOT_MARKET_ALARM=on.

    It reads the committed calendars and the research files under <repo>/data (earnings, alarm/top10.csv); a missing
    file makes its flag unknown. The engine only writes the result as an event: no order path reads it."""
    if not _switch("KAIBOT_MARKET_ALARM", env):
        return None
    from kaibot.features.market_alarm import live_alarm

    log.info("market alarm: ON (a logged notice; it gates nothing)")
    return live_alarm


def daily_path(data=None, review=None, shadow=None, note=None):
    """#206: the live daily path's factory, only with KAIBOT_MULTIDAY=on and a multi-day rule enabled in
    config/profiles.yaml (`multi_day.rules`); None otherwise, and then nothing of it is imported. The daily view is
    read from the daily store under KAIBOT_DAILY_DATA (default <repo>/data, the folder holding daily/, which
    ./scripts/fetch_daily.sh refreshes; docs/ops/paper_start.md)."""
    from kaibot.strategies import registry

    if not registry.multi_day_enabled():
        return None
    from kaibot.risk.profiles import enabled_multi_day

    if not enabled_multi_day():
        return None

    from kaibot.engine import live_daily

    root = _switch("KAIBOT_DAILY_DATA") or ROOT / "data"
    load = live_daily.StoreLoader(root, ROOT / "config" / "universe_history.yaml")
    log.info("multi-day daily path: ON (%s; daily store %s)", ", ".join(enabled_multi_day()), root / "daily")
    from kaibot.engine.corp_guard import CorporateActionGuard

    # #266 (trend_momentum.md Amendment 6): no buy of a stock with an announced split, spin-off, merger or symbol
    # change in the next 35 sessions, read from Alpaca's market-data corporate actions (free)
    # QA-Ops (#271): the read runs inside the engine's loop (the send step at 09:31): each request is bounded
    # (bounded_data_get) and the guard stops the whole read at 25 s (a failed read: no buys)
    ca = CorporateActionGuard(get=bounded_data_get(data)) if data is not None else None
    if review is not None:
        log.warning("pre-open review of the daily path: ON (KAIBOT_PREOPEN_REVIEW=on, %s)",
                    "LLM key set" if review.gateway.enabled else "no ANTHROPIC_API_KEY: no daily buys until it is set")
    if shadow is not None:
        log.warning("pre-open review in SHADOW mode (KAIBOT_PREOPEN=shadow): observer only, no effect on orders (%s)",
                    "LLM key set" if shadow.gateway.enabled else "no ANTHROPIC_API_KEY: every session is skipped")
    if note is not None:
        log.info("daily note in plain words: ON (KAIBOT_DAILY_NOTE=on, %s)",
                 "Haiku" if note.gateway.enabled else "no ANTHROPIC_API_KEY: the code's template")
    flags = filing_flags_from_env(os.environ)  # #375: None unless KAIBOT_FILING_FLAG=shadow
    if flags is not None:
        log.warning("8-K filing flags in SHADOW mode (KAIBOT_FILING_FLAG=shadow): logged only, no effect on orders")
    checks = daily_entry_checks(root)  # M12 3.11 (#473): None only with KAIBOT_DAILY_CHECKS_BLOCK=off (#695)
    return lambda eng: live_daily.for_engine(eng, load, ca=ca, review=review, shadow=shadow, note=note,
                                             filing_flags=flags, entry_checks=checks)


def daily_entry_checks(root, env=None):
    """M12 3.11 (#473): the daily-store data checks that block buys of the multi-day path (kaibot.ingest.entry_checks),
    or None with KAIBOT_DAILY_CHECKS_BLOCK=off (then nothing is read, nothing changes). On by default since audit 2
    5.15 (#695): every check but price_jump. Sales, exits and stops never ask. Takes effect at the engine's start."""
    from kaibot.common import switches

    codes = switches.value("KAIBOT_DAILY_CHECKS_BLOCK", env)
    if not codes:
        return None
    from pathlib import Path

    from kaibot.ingest.entry_checks import DailyEntryChecks

    log.warning("daily-data checks that block buys: ON (%s; daily store %s). Sales and stops are never blocked",
                ", ".join(codes), Path(root) / "daily")
    return DailyEntryChecks(Path(root) / "daily", frozenset(codes))


def filing_flags_from_env(env):
    """#375: the 8-K filing-flag observer (kaibot.features.filing_flag), or None unless KAIBOT_FILING_FLAG=shadow.
    Imported only when switched on; the daily path writes its answer as an event and reads nothing back."""
    if _switch("KAIBOT_FILING_FLAG", env) != "shadow":
        return None
    from kaibot.features.filing_flag import from_env

    return from_env(env)


EXIT_JOURNAL = 4
EXIT_DAILY_PATH = 5  # M10 1.20 step 2 (#445): multi-day sleeves in the ledger and no daily path (KAIBOT_MULTIDAY)


def open_journal(path) -> Journal:
    """M9 1.18 (#443): the journal, or exit code 4 when its file is not durable (write-ahead mode, full sync,
    `fullfsync` on macOS). Before the lease and before anything is read from the broker: nothing was done."""
    try:
        return Journal(path)
    except (DurabilityError, JournalCorrupt) as e:  # #496: an entry that fails its checksum is never folded
        log.error("%s", e)
        raise SystemExit(EXIT_JOURNAL) from None
    except sqlite3.DatabaseError as e:
        # #770: a file SQLite itself cannot read ("malformed database schema", "file is not a database") is a
        # damaged journal too; a passing state (OperationalError: locked, busy) is not, and is raised as it is
        if type(e) is not sqlite3.DatabaseError:
            raise
        log.error("the journal file cannot be read: %s. Not folded. See docs/ops/ledger.md, 'Checksums'.", e)
        raise SystemExit(EXIT_JOURNAL) from None


def multi_day_state(remote=None) -> dict:
    """`--check` (#133): the multi-day switches as this engine would start with them, read-only. `KAIBOT_MULTIDAY`
    (environment), `all_levels.allow_overnight` (config/profiles.yaml), the multi-day rules it would load, and the
    experiment label at the active run's level (decision 15 section 11)."""
    from kaibot.risk.profiles import enabled_multi_day, experiments, rule_book_limits
    from kaibot.strategies import registry

    cfg = yaml.safe_load((ROOT / "config" / "profiles.yaml").read_text())
    on = registry.multi_day_enabled()
    run = remote.active_run() if remote is not None else None
    level = (run or {}).get("level") or "balanced"
    rules = enabled_multi_day() if on else []
    return {registry.MULTIDAY_FLAG: "on" if on else "off",
            "allow_overnight": bool(cfg["all_levels"].get("allow_overnight", False)),
            "rules_loaded": rules, "level": level,
            "overnight_cap_pct": {r: rule_book_limits(r, level).max_overnight_pct for r in rules},
            "experiments": experiments(rules, level), **({"daily_store": daily_store_state()} if rules else {})}


def daily_path_state(eng: Engine, remote) -> dict | str:
    """`--check` (#643 follow-up): would a start be refused with exit code 5 (the active run's ledger holds
    multi-day sleeves and this engine has no daily path)? Read-only: the same question `Engine.startup` asks, of the
    ledger folded from the journal; no lease, no order, no journal entry, no event. A refusal is a FAILED line with
    the engine's own words (the fix names KAIBOT_MULTIDAY or the rule), so `--check` ends with exit code 1."""
    run = remote.active_run()
    refusal = eng.daily_path_refusal(run["id"]) if run else None
    if refusal is not None:
        return f"FAILED: a start would be refused (exit code {EXIT_DAILY_PATH}): {refusal}"
    sleeves = sorted({sym for _, sym in eng._md_sleeves(run["id"])}) if run else []
    return {"daily_path": "on" if eng.daily is not None else "off", "multi_day_sleeves": sleeves,
            "start": "allowed"}


def daily_store_state() -> dict:
    """#206: where the daily path reads its daily view, and the last session the store holds (SPY)."""

    from kaibot.engine.live_daily import store_state

    return store_state(_switch("KAIBOT_DAILY_DATA") or ROOT / "data")


def report_conflicts(eng: Engine, replicator: Replicator) -> None:
    """Rows Supabase refused as duplicates that are not the engine's (B2 of the #117 review): replication went on;
    each one becomes an event and a halt row the dashboard shows. Trading is not affected (its state is local).
    M9 1.12: also the poison rows (refused for good, skipped) and a backlog that grows for ten minutes."""
    for f in replicator.take_fallbacks():  # QA-Ops on #339: migration 0028 not applied yet (rows say "missing")
        with contextlib.suppress(Exception):
            eng.store.event(f.get("event", "stop_state_fallback"), {k: f[k] for k in ("seq", "error", "action")},
                            eng._rid())
    for c in replicator.take_conflicts():
        with contextlib.suppress(Exception):
            detail = {k: c[k] for k in ("seq", "table", "key", "reason")}
            eng.store.event("replica_conflict", detail, eng._rid())
            eng.store.insert("halt", {"run_id": eng._rid(), "kind": "replica_conflict",
                                      "reason": f"Supabase holds another row for {c['table']} {c['key']}: "
                                                "the journal keeps the engine's (docs/ops/ledger.md)"},
                             returning=False)
    for p in replicator.take_poisons():  # M9 1.12 (#437): a row Supabase refuses for good was skipped
        log.error("replica poison: Supabase refuses %s entry %s for good (%s); skipped, the journal keeps it",
                  p["table"], p["seq"], str(p["error"])[:160])
        with contextlib.suppress(Exception):
            eng.store.event("replica_poison", {k: p[k] for k in ("seq", "table", "op", "code", "error", "attempts",
                                                                 "action")}, eng._rid())
            eng.store.insert("halt", {"run_id": eng._rid(), "kind": "replica_poison",
                                      "reason": f"Supabase refuses {p['table']} rows ({p['code']}): skipped, the "
                                                "journal keeps them (docs/ops/ledger.md)"}, returning=False)
    b = None
    with contextlib.suppress(Exception):
        b = replicator.backlog_alarm()
    if b:  # M9 1.12: rows waiting for ten minutes and not getting fewer
        log.error("replica backlog: %d rows waiting for %.0f minutes (%s): the dashboard and the dead-man's book "
                  "are stale", b["pending"], b["minutes"], b["error"] or "no error")
        with contextlib.suppress(Exception):
            eng.store.event("replica_backlog", b, eng._rid())


def journal_upkeep(eng: Engine, status, now: datetime, done: dict) -> None:
    """M10 1.24 (#449): the journal's own housekeeping, with the market closed and at most once a day (UTC).

    - Every entry's checksum is checked again (#496). Reads of the small kinds are answered from memory between
      starts, so this is what notices a file damaged underneath a running engine; JournalCorrupt goes to the loop,
      which stops the engine as for any damaged entry.
    - A snapshot is written when one is due (none yet, a new month, a new run): the next start folds from it.
      It is written only if a fold of the whole journal gives the state in memory; a difference is an event and
      an ERROR line, no snapshot, and the engine goes on (the next start folds everything, as before)."""
    store = eng.store
    if not isinstance(status, dict) or status.get("state") != "market_closed" or not hasattr(store, "snapshot_due"):
        return
    day = now.astimezone(UTC).date()
    if done.get("day") == day:
        return
    done["day"] = day
    store.journal.verify()
    why = store.snapshot_due(now)
    if why is None:
        return
    try:
        got = store.snapshot(why)
        log.info("journal snapshot written (%s): entry %d covers entries up to %d, %d rows", why, got["seq"],
                 got["upto"], got["rows"])
    except SnapshotMismatch as e:
        log.error("%s", e)
        with contextlib.suppress(Exception):
            eng.store.event("journal_snapshot_mismatch", {"error": str(e)[:300]}, eng._rid())


def report_news(eng: Engine, poller) -> None:
    """The news poller's health changes (news_feed_unhealthy / news_feed_ok) and the headline feed's
    (headline_feed_failed / headline_feed_ok), written from the main thread."""
    if poller is None:
        return
    for kind, detail in poller.take_events():
        with contextlib.suppress(Exception):
            eng.store.event(kind, detail, eng._rid())


def fence_orders(eng, lease: EngineLease) -> None:
    """#477: give the engine's order fence (engine/fence.FencedBroker) this lease. Called at a normal start, at a
    degraded start (before its start-up's first order call) and again when `confirm_lease` succeeds. The fence
    follows the lease's state: while degraded and not held, a fenced call that only reduces goes out (sales, stops,
    cancels, as the outage design wants) and an entry is refused; once the lease is taken (with its epoch) every
    order call first renews with that epoch, and a stale tenure is refused and stops the engine (QA-Ops on #584:
    after #536's degraded start the fence never got its tenure and stayed off once fully active)."""
    broker = getattr(eng, "broker", None)
    if isinstance(broker, FencedBroker):
        broker.lease = lease


def start_with_lease(eng: Engine, lease: EngineLease, adopt_journal: bool = False) -> str:
    """Take the cross-machine lease, then start the engine. Without the lease nothing else happens: no order
    adoption, no start event, no heartbeat. Exits with code 3 and a message that says what to do. With the lease,
    a new journal first copies the active run from Supabase (S77); if it cannot, exit code 4. Then the journal
    must be the newest one Supabase has seen (not a stale standby, not a restored backup); if not, exit code 4
    and the reason, unless `adopt_journal` (`--adopt-journal`, docs/ops/ledger.md).

    M10 1.25 (#450): when the lease cannot be CHECKED (Supabase unreachable) the engine may start "reducing only"
    (`degraded_start`). Returns "active" or "degraded"."""
    try:
        got = lease.acquire()
    except LeaseUnavailable as e:
        degraded_start(eng, lease, e)
        return "degraded"
    except LeaseError as e:
        log.error("%s", e)
        raise SystemExit(EXIT_LEASE) from None
    eng.lease = lease
    fence_orders(eng, lease)  # #477: from here on every order call is fenced by this tenure
    log.info("engine lease taken (host %s, %d s, renewed with every heartbeat)", lease.host, lease.ttl_s)
    if got.get("taken_over"):
        log.warning("took over an expired lease from host %r", got.get("previous_host"))
        with contextlib.suppress(Exception):
            eng.store.event("engine_lease_takeover", {"host": lease.host, "previous_host": got.get("previous_host")})
    if hasattr(eng.store, "bootstrap"):
        try:
            out = eng.store.bootstrap(eng.process)
            eng.store.verify_identity(eng.process, adopt=adopt_journal)
        except (BootstrapError, IdentityError) as e:
            log.error("%s", e)
            lease.release()
            raise SystemExit(EXIT_JOURNAL) from None
        if out.get("imported"):
            log.info("journal started: %d rows of run %s copied from Supabase", out["imported"], out.get("run_id"))
    eng.startup()
    return "active"


def start_or_refuse(eng: Engine, lease: EngineLease, adopt_journal: bool = False) -> str:
    """`start_with_lease`, and M10 1.20 step 2 (#445, Product ruling 24 Nov): an engine whose run holds multi-day
    sleeves without the daily path (KAIBOT_MULTIDAY off) does not start. `Engine.startup` raises before any order
    call; the lease is given back and the process exits with code 5 and a message naming the switch."""
    try:
        return start_with_lease(eng, lease, adopt_journal=adopt_journal)
    except DailyPathMissing as e:
        log.error("%s", e)
        lease.release()  # never raises; a degraded start never held it
        raise SystemExit(EXIT_DAILY_PATH) from None


# ---------------------------------------------------------------- degraded start (M10 1.25, #450)
FAILSAFE_IDS = ("kbwd-", "kbdm-")  # the watchdog's and the dead-man's stops: not an engine
EVIDENCE_LOOKBACK_S = 300.0  # fills are read from this long before the journal's last entry
EVIDENCE_LOOKUPS = 40  # orders looked up by id for the fills; more unknown fills than this: not ruled out


def _engine_id(coid) -> bool:
    """A client order id an engine gives its orders (`kb-<run>-...`, `kb<run>-...`, `kbfl-...`)."""
    c = str(coid or "")
    return c.startswith("kb") and not c.startswith(FAILSAFE_IDS)


def other_engine_evidence(broker, journal) -> list[str]:
    """The broker's word on whether another engine traded this account after this journal's last entry: open
    orders, and fills since shortly before that entry, that carry an engine's client order id this journal does
    not know. An engine journals every order's client id BEFORE it sends it (`om_intent`, `md_exit_intent`,
    `intent`; a replace's new id in `om_state`), so an engine id the journal lacks was sent by another engine.
    The fail-safes' stops (`kbwd-`, `kbdm-`) and orders without an engine id are not evidence (a stranger's order
    is the account check's business: it blocks entries). Anything that cannot be read counts as evidence."""
    coids: set[str] = set()
    ids: set[str] = set()
    for _, _, _, b in journal.entries(kinds=("om_intent", "om_state", "md_exit_intent", "md_exit", "intent",
                                             "own_order_ids")):
        coids |= {str(b[k]) for k in ("coid", "client_id", "client_order_id") if b.get(k)}
        ids |= {str(i) for i in [b.get("order_id"), *(b.get("ids") or [])] if i}
    last = journal.entries(after=max(journal.last_seq() - 1, 0))
    since = datetime.fromisoformat(last[-1][1]) - timedelta(seconds=EVIDENCE_LOOKBACK_S) if last else None
    out: list[str] = []

    def look(o: dict, what: str) -> None:
        for x in [o, *(o.get("legs") or [])]:
            coid = x.get("client_order_id")
            if _engine_id(coid) and str(coid) not in coids and str(x.get("id")) not in ids:
                out.append(f"{what} {coid} ({x.get('side')} {x.get('symbol')})")

    try:
        for o in broker.open_orders():
            look(o, "open order")
        asked: set[str] = set()
        for a in broker.activities(since, types=("FILL",)):
            oid = str(a.get("order_id"))
            if oid in ids or oid in asked:  # ours, or looked up already
                continue
            asked.add(oid)
            if len(asked) > EVIDENCE_LOOKUPS:
                out.append(f"more than {EVIDENCE_LOOKUPS} fills on orders this journal does not know")
                break
            look(broker.order(oid), "fill on order")
    except Exception as e:
        out.append(f"the broker cannot be read ({type(e).__name__}: {str(e)[:120]})")
    return out


def degraded_start(eng: Engine, lease: EngineLease, err: Exception) -> None:
    """M10 1.25 (#450): the lease could not be checked. Start "reducing only" when (1) the journal's last lease
    record is this install's own and (2) the broker shows no order of another engine since the journal's last
    entry; otherwise exit code 3, as before. Reducing only: `lease.ok` stays False, so every buy is refused
    (`Engine.entry_block`: "lease unverified"); exits, stops, sales and fills go on. No command is applied, no
    heartbeat is written and no row is sent to Supabase until `confirm_lease` has taken the lease and verified
    the journal (docs/ops/failsafe.md, "Degraded start")."""
    journal = getattr(eng.store, "journal", None)
    ok, why = (lease.degraded_allowed() if journal is not None and lease.journal is not None
               else (False, "this engine keeps no local journal"))
    if not ok:
        log.error("%s No degraded start: %s.", err, why)
        raise SystemExit(EXIT_LEASE)
    evidence = other_engine_evidence(eng.broker, journal)
    if evidence:
        lease.contradicted(evidence)
        log.error("%s No degraded start: the broker shows that another engine has traded this account since "
                  "this journal's last entry, or the broker cannot be read: %s.", err, "; ".join(evidence[:5]))
        raise SystemExit(EXIT_LEASE)
    lease.start_degraded(why)
    eng.lease = lease
    fence_orders(eng, lease)  # #477 (QA-Ops on #584): reducing only at the fence too, before the first order call
    log.warning("The engine lease could not be checked (Supabase unreachable?). Starting REDUCING ONLY: %s and the "
                "broker shows no other engine's orders. Exits, stops, sales and fills are managed; nothing is "
                "bought, no command is applied and nothing is sent to Supabase until the lease is confirmed "
                "(docs/ops/failsafe.md, 'Degraded start').", why)
    eng.startup()
    with contextlib.suppress(Exception):
        eng.store.event("engine_degraded_start", {"why": why, "host": lease.host, "error": str(err)[:200]},
                        eng._rid())


def confirm_lease(eng: Engine, lease: EngineLease, adopt_journal: bool = False) -> bool:
    """A degraded engine asks again, once per loop pass. True: the lease is ours and the journal is the newest one
    Supabase has seen: fully active from here on. False: still not known (Supabase unreachable): stay reducing
    only. Another engine holds the lease: exit code 3. The lease was free but another journal ran the engine
    meanwhile (this one is stale): the lease is given back, exit code 4. Nothing is bought before True."""
    if not lease.held:
        try:
            got = lease.acquire()  # held and fresh, but `ok` stays False while `degraded` is set
        except LeaseUnavailable:
            return False
        except LeaseHeld as e:
            log.error("%s", e)
            raise SystemExit(EXIT_LEASE) from None
        if got.get("taken_over"):
            log.warning("the lease was held by host %r while this engine was reducing only",
                        got.get("previous_host"))
    try:
        eng.store.verify_identity(eng.process, adopt=adopt_journal)
    except IdentityUnreadable as e:
        log.warning("lease taken, journal not verified yet (%s): still reducing only", str(e)[:160])
        return False
    except IdentityError as e:
        log.error("%s", e)
        lease.release()
        raise SystemExit(EXIT_JOURNAL) from None
    lease.activate()
    fence_orders(eng, lease)  # #477: the confirmed tenure (its epoch) fences every order call from here on
    # #536 (test-eng): a session that began while Supabase was down could not read the owner's config and refused
    # entries; it is read again now. "Entries allowed" is said only when nothing else blocks them either.
    still = retry_rules(eng, force=True)
    block = still and f"config refused: {still}"
    if block is None:
        entry_block = getattr(eng, "entry_block", None)
        block = entry_block() if entry_block is not None else None
    if block is None:
        log.info("engine lease confirmed and journal verified: fully active (entries allowed)")
    elif still:
        log.warning("engine lease confirmed and journal verified, but entries stay OFF: %s (%s; exits, stops and "
                    "sales go on)", block, f"the owner's config is asked again every {RULES_RETRY_S:.0f} s"
                    if still.startswith(CONFIG_UNREADABLE) else "until the next session")
    else:
        log.info("engine lease confirmed and journal verified: active (no entries for now: %s)", block)
    with contextlib.suppress(Exception):
        eng.store.event("engine_lease_confirmed", {"host": lease.host, "entries": block is None,
                                                   **({"entries_off": str(block)[:200]} if block else {})},
                        eng._rid())
    return True


def retry_rules(eng: Engine, force: bool = False) -> str | None:
    """`Engine.retry_rules` where the engine has it (the start-up tests' stand-in does not)."""
    retry = getattr(eng, "retry_rules", None)
    return retry(force=force) if retry is not None else None


def before_tick(eng: Engine, lease: EngineLease, adopt_journal: bool = False) -> bool:
    """The start of every loop pass. True on the pass a degraded engine became fully active (the caller then
    starts what waits for that: replication, the news poller). While degraded no command is applied: a command
    marked applied here would be taken from the engine that holds the lease."""
    became = False
    if lease.degraded:
        if not confirm_lease(eng, lease, adopt_journal):
            if lease.held:  # taken, identity not verified yet: keep it alive
                lease.renew()
            return False
        became = True
    if not lease.ok:  # a slow or failed step may have let it lapse: prove it is ours before acting
        lease.renew(force=True)
    if lease.ok and not became:  # #536: a config unreadable at the session's start is asked again (once a minute)
        retry_rules(eng)
    eng.apply_commands()
    return became


def write_pid_file(data_dir, host: str) -> None:
    """M9 1.6: this process, for the watchdog's local check when it cannot read the heartbeat (engine/watchdog.py
    `LocalEngine`). Left in place at a stop: a pid that is gone is exactly what the watchdog must see. Never fatal."""
    from pathlib import Path

    from kaibot.engine.failsafe import PID_FILE

    try:
        path = Path(data_dir) / PID_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "host": host, "started": datetime.now(UTC).isoformat()}))
        tmp.replace(path)
    except Exception:
        log.exception("pid file not written: the watchdog will judge the engine by its journal alone")


def make_beat(eng: Engine, lease: EngineLease, host: str, stop: dict):
    """The heartbeat, which also renews the lease. A failed renewal leaves `lease.ok` False (no entries until it
    works again); a lease that now belongs to another engine sets `stop` so the main loop ends."""

    def beat(st: dict) -> None:
        if lease.degraded:  # M10 1.25: the heartbeat row and the lease belong to whoever holds the lease
            return
        try:
            lease.renew()
        except LeaseLost as e:
            if not stop.get("lease_lost"):
                log.error("%s", e)
            stop.update(now=True, lease_lost=True)
            return  # the heartbeat row now belongs to the engine that holds the lease
        try:  # #446: the trading state and the entry block, on every beat (a report: never stops the beat)
            trading = eng.trading_detail()
            trading = dict(trading) if isinstance(trading, dict) else {}
        except Exception:
            trading = {}
        try:
            eng.store.heartbeat(eng.process, {**st, **trading, "host": host, "ts": datetime.now(UTC).isoformat(),
                                              "lease": lease.status()})
        except Exception:
            log.exception("heartbeat failed")

    return beat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify keys and connectivity, then exit")
    ap.add_argument("--adopt-journal", action="store_true",
                    help="start even though Supabase has rows from another or a newer journal (docs/ops/ledger.md)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    eng = build(settings)
    if a.check:
        report: dict = {}

        def step(name, fn):
            try:
                report[name] = fn()
            except Exception as e:  # report every failure, don't stop at the first
                report[name] = f"FAILED: {str(e)[:160]}"

        step("alpaca_paper", lambda: {k: eng.broker.account()[k] for k in ("status", "equity")})
        step("market", lambda: {k: eng.broker.clock()[k] for k in ("is_open", "next_open")})
        step("positions_in_account", lambda: sorted(eng.broker.positions()))
        step("market_data", lambda: f"{len(eng.market.data.latest_quotes(['SPY']))} quote(s) from IEX")

        remote = getattr(eng.store, "remote", None) or eng.store

        def supa():
            remote.heartbeat("engine-check", {"state": "check", "host": socket.gethostname()})
            if not remote.select("heartbeat", {"process": "eq.engine-check"}):
                raise RuntimeError("write/read did not round-trip")
            run = remote.active_run()
            return {"write_read": "ok", "active_run": run and {"id": run["id"], "status": run["status"]}}

        step("supabase", supa)

        def lease_state():  # read-only: --check never takes the lease
            row = eng.store.lease_row()
            return row and {k: row[k] for k in ("host", "started_at", "expires_at")} or "free"

        step("engine_lease", lease_state)
        def journal_state():
            rep = Replicator(eng.journal, remote)
            rep.detect()  # "schema": "0018", or "pre-0018" (the dashboard still updates; apply 0018, then restart)
            return {"path": eng.journal.path, **rep.status()}

        step("journal", journal_state)
        step("multi_day", lambda: multi_day_state(remote))
        step("daily_path", lambda: daily_path_state(eng, remote))
        def news_state():  # read-only: one request, nothing stored
            now = datetime.now(UTC)
            n = len(eng.market.data.news(["SPY"], now - pd.Timedelta(hours=72), now, max_pages=1))
            return {"spy_stories_72h": n, "poller": "on" if getattr(eng, "news_poller", None) else "off"}

        step("alpaca_news", news_state)
        if eng.intraday:  # the VIX is read by the intraday regime filter only (M10 1.19)
            step("fred_vix", lambda: round(float(eng.market.vix_loader().dropna().iloc[-1]), 2))
        report["intraday_path"] = "on" if eng.intraday else "off"
        print(json.dumps(report, indent=1, default=str))
        ok = not any(isinstance(v, str) and v.startswith("FAILED") for v in report.values())
        print("ALL GOOD" if ok else "Fix the FAILED lines above, then run --check again.")
        raise SystemExit(0 if ok else 1)
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    host = socket.gethostname()
    lease = EngineLease(eng.store, holder_id(settings.data_dir, host), host, journal=eng.journal)
    # QA-Ops (24-25 Nov 2026 incident): the last heartbeat, read before this engine writes its own (engine/gap.py)
    gaps = GapWatch(eng.store, eng._rid, host, process=eng.process)
    with contextlib.suppress(Exception):  # a report only: never able to stop a start
        gaps.before_start(getattr(eng.store, "remote", None) or eng.store)
    mode = start_or_refuse(eng, lease, adopt_journal=a.adopt_journal)
    with contextlib.suppress(Exception):
        gaps.after_start()  # one engine_gap event if the engine was down more than an hour without a clean stop
    fetch = None  # audit 2, 5.12 (#692): the daily-bars fetch the engine starts itself (engine/fetch.py)
    with contextlib.suppress(Exception):
        fetch = engine_fetch.for_engine(eng)
    if fetch is not None:
        fetch.wake("start")
    write_pid_file(settings.data_dir, host)
    replicator = Replicator(eng.journal, eng.store.remote)  # S77: Supabase catches up in the background
    news = getattr(eng, "news_poller", None)
    feed = getattr(eng, "headline_feed", None)

    def go_active() -> None:
        """What only the engine that holds the lease does: send its rows to Supabase, poll the news. At once
        after a normal start; after a degraded one (M10 1.25) when the lease is confirmed."""
        # Once, now: with 0018 the new tables; without it the pre-0018 ones (the dashboard keeps updating).
        # Supabase unreachable: asked again at the first pass that reaches it.
        log.info("replica schema: %s", replicator.detect() or "not known yet (Supabase unreachable)")
        replicator.start()
        if news is not None:
            news.start()
            log.info("news poller: on (every %.0f s now; heartbeat `news`)", news.every_s())
        if feed is not None:
            feed.start()
            log.info("headline feed: on (EDGAR filings %s)", feed.status()["edgar"])

    if mode == "active":
        go_active()
    status: dict = {"state": "starting"}
    last_minute = None
    upkeep: dict = {}
    if getattr(eng.store, "snapshot_problem", None):  # the newest snapshot was refused: everything was folded
        with contextlib.suppress(Exception):
            eng.store.event("journal_snapshot_unused", {"error": eng.store.snapshot_problem[:300]}, eng._rid())
    log.info("engine up (code %s). Ctrl-C to stop; broker-held stops stay in place.", eng.code)
    last_error = ""

    beat = make_beat(eng, lease, host, stop)

    eng.on_progress = lambda st: beat(st)  # long steps (history load) report progress
    while not stop["now"]:
        with contextlib.suppress(Exception):
            gaps.tick()  # a pass more than an hour after the last one: the process was frozen (the Mac asleep)
        try:
            if lease.lost:  # #477: the fence (or a renewal) found the tenure gone: nothing more is sent
                raise LeaseLost("The engine lease is no longer ours (found by the order fence). Stopping.")
            if before_tick(eng, lease, a.adopt_journal):
                go_active()
            now = datetime.now(UTC)
            minute = now.replace(second=0, microsecond=0)
            if minute != last_minute and now.second >= 7:
                status = eng.tick()
                eng.on_tick_ok()
                last_minute = minute
                if status.get("state") != "market_closed":
                    log.info("tick %s", status)
                journal_upkeep(eng, status, now, upkeep)
        except LeaseLost as e:
            log.error("%s", e)
            stop.update(now=True, lease_lost=True)
            break
        except JournalCorrupt as e:
            # #496: the book is a fold of the journal, so nothing more is decided on it. The engine stops; the
            # broker-held stops stay, the watchdog covers the rest, and the next start refuses the file (code 4).
            log.error("%s", e)
            with contextlib.suppress(Exception):
                eng.store.event("engine_error", {"error": f"JournalCorrupt: {e}"[:300]}, eng._rid())
            stop.update(now=True, journal_corrupt=True)
            break
        except Exception as e:
            log.exception("loop error (will retry)")
            with contextlib.suppress(Exception):
                eng.on_tick_error(e)  # repeated failures trip the kill switch (fail closed)
            msg = f"{type(e).__name__}: {e}"[:300]
            status = {"state": "error", "error": msg}
            if msg != last_error:  # record each new error once, so it shows up without the terminal
                last_error = msg
                with contextlib.suppress(Exception):
                    eng.store.event("engine_error", {"error": msg}, eng._rid())
        if fetch is not None:
            fetch.step()  # on waking, or asked at the start: checks the store and starts a fetch; polls a running one
        report_conflicts(eng, replicator)
        report_news(eng, news)
        report_news(eng, feed)  # headline_feed_failed / _ok, written from this thread (the event chain)
        status = {**status, "replication": replicator.status()} if isinstance(status, dict) else status
        if news is not None and isinstance(status, dict):
            status = {**status, "news": news.status()}
        beat(status)
        for _ in range(10):
            if stop["now"]:
                break
            time.sleep(1)
    if lease.degraded:
        # M10 1.25: stopped while still reducing only. Nothing was sent to Supabase and nothing is now (no
        # heartbeat, no rows: the journal was never verified); the journal keeps everything for the next start.
        lease.release()  # only if it was taken and the journal not yet verified
        log.info("engine stopped (it was reducing only: nothing was sent to Supabase)")
        return
    if news is not None:
        news.stop()
    if feed is not None:
        feed.stop()
    if stop.get("lease_lost"):
        # Another engine is in charge: write nothing. Its heartbeat and lease are not ours, and an event from
        # here would fork the hash-chained event log it is now appending to. Its `engine_lease_takeover` event
        # is the record; this side has the log line.
        log.error("engine stopped: lease lost (positions it was managing: %s)", sorted(eng.pos))
        replicator.stop(flush_s=5.0)  # our own rows only (engine-assigned ids): safe to finish sending
        raise SystemExit(EXIT_LEASE)
    # #311: the daily note, if the engine stops after the close before its 16:10 ET tick wrote it (off: a no-op)
    note_final = getattr(getattr(eng, "daily", None), "note_final", None)
    if note_final is not None:
        with contextlib.suppress(Exception):
            note_final()
    try:
        eng.store.heartbeat(eng.process, {"state": "stopped", "host": host})
        eng.store.event("engine_stop", {"positions": list(eng.pos)}, eng._rid())
    except Exception:
        pass
    replicator.stop()
    lease.release()  # clean stop: another machine may start at once
    if stop.get("journal_corrupt"):
        log.error("engine stopped: the journal is damaged (docs/ops/ledger.md, 'Checksums')")
        raise SystemExit(EXIT_JOURNAL)
    log.info("engine stopped")


if __name__ == "__main__":
    main()
