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
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import yaml

from kaibot.agents.gateway import from_env as gateway_from_env
from kaibot.agents.room import Room
from kaibot.common import http
from kaibot.engine.alpaca import AlpacaData, AlpacaPaper
from kaibot.engine.config import ROOT, Settings
from kaibot.engine.core import Engine
from kaibot.engine.dividends import ExDateDividends
from kaibot.engine.events import load_calendar
from kaibot.engine.journal import BootstrapError, IdentityError, Journal, JournalStore, Replicator, journal_path
from kaibot.engine.lease import EXIT_LEASE, EngineLease, LeaseError, LeaseLost, holder_id
from kaibot.engine.m3glue import LockedDesk, make_pack_fn, make_rules_extra
from kaibot.engine.market import LiveMarket
from kaibot.engine.store import Store
from kaibot.news import NewsDesk
from kaibot.news import configure as configure_news
from kaibot.risk.profiles import load_profiles, load_strategies

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


def build(settings: Settings) -> Engine:
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    etf_of = {s: etf for etf, syms in cfg["sectors"].items() for s in syms}
    broker = AlpacaPaper(settings.alpaca_key, settings.alpaca_secret)
    data = AlpacaData(settings.alpaca_key, settings.alpaca_secret)
    market = LiveMarket(
        data, sorted(etf_of), etf_of, cfg["market"], settings.data_dir / "history", vix_loader(settings.fred_key)
    )
    remote = Store(settings.supabase_url, settings.supabase_service_key)
    # S77: the engine's own tables live in the local journal, written before any broker call; Supabase is a replica
    store = JournalStore(Journal(journal_path(settings.data_dir)), remote, live=True)
    events, covers_until = load_calendar(ROOT / "config" / "macro_calendar.yaml")
    fomc = {np.datetime64(d, "D") for d, evs in events.items() if any(e == "fomc_statement_1400" for e, _ in evs)}
    gw = gateway_from_env(remote, os.environ)  # S32/S34: the room runs only with ANTHROPIC_API_KEY and KAIBOT_ROOM=on
    room = Room(gw) if gw.enabled else None
    log.info("agent room: %s", "ON (Sonnet, capped budget)" if room else "off (rules-only)")
    # S33: one news desk per engine; it triages through the gateway only when the room is on.
    desk = NewsDesk(remote, source=data, gateway=gw if gw.enabled else None)
    try:
        log.info("news window: %d items reloaded", desk.load_window())
    except Exception:
        log.exception("news window reload failed: starting empty")
    configure_news(desk)
    locked = LockedDesk(desk)
    return Engine(broker, market, store, load_profiles(), fomc, process=settings.engine_name,
                  calendar_until=covers_until, events=events, strategies=load_strategies(), room=room,
                  pack_fn=make_pack_fn(locked), rules_extra=make_rules_extra(locked),
                  ex_dividends=ExDateDividends(data),  # S78: dividends accrued at the ex-date (#118)
                  daily=daily_path())


def daily_path():
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
    from pathlib import Path

    from kaibot.engine import live_daily

    root = Path(os.environ.get("KAIBOT_DAILY_DATA") or ROOT / "data")
    load = live_daily.StoreLoader(root, ROOT / "config" / "universe_history.yaml")
    log.info("multi-day daily path: ON (%s; daily store %s)", ", ".join(enabled_multi_day()), root / "daily")
    return lambda eng: live_daily.for_engine(eng, load)


EXIT_JOURNAL = 4


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


def daily_store_state() -> dict:
    """#206: where the daily path reads its daily view, and the last session the store holds (SPY)."""
    from pathlib import Path

    from kaibot.engine.live_daily import store_state

    return store_state(Path(os.environ.get("KAIBOT_DAILY_DATA") or ROOT / "data"))


def report_conflicts(eng: Engine, replicator: Replicator) -> None:
    """Rows Supabase refused as duplicates that are not the engine's (B2 of the #117 review): replication went on;
    each one becomes an event and a halt row the dashboard shows. Trading is not affected (its state is local)."""
    for c in replicator.take_conflicts():
        with contextlib.suppress(Exception):
            detail = {k: c[k] for k in ("seq", "table", "key", "reason")}
            eng.store.event("replica_conflict", detail, eng._rid())
            eng.store.insert("halt", {"run_id": eng._rid(), "kind": "replica_conflict",
                                      "reason": f"Supabase holds another row for {c['table']} {c['key']}: "
                                                "the journal keeps the engine's (docs/ops/ledger.md)"},
                             returning=False)


def start_with_lease(eng: Engine, lease: EngineLease, adopt_journal: bool = False) -> None:
    """Take the cross-machine lease, then start the engine. Without the lease nothing else happens: no order
    adoption, no start event, no heartbeat. Exits with code 3 and a message that says what to do. With the lease,
    a new journal first copies the active run from Supabase (S77); if it cannot, exit code 4. Then the journal
    must be the newest one Supabase has seen (not a stale standby, not a restored backup); if not, exit code 4
    and the reason, unless `adopt_journal` (`--adopt-journal`, docs/ops/ledger.md)."""
    try:
        got = lease.acquire()
    except LeaseError as e:
        log.error("%s", e)
        raise SystemExit(EXIT_LEASE) from None
    eng.lease = lease
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


def make_beat(eng: Engine, lease: EngineLease, host: str, stop: dict):
    """The heartbeat, which also renews the lease. A failed renewal leaves `lease.ok` False (no entries until it
    works again); a lease that now belongs to another engine sets `stop` so the main loop ends."""

    def beat(st: dict) -> None:
        try:
            lease.renew()
        except LeaseLost as e:
            if not stop.get("lease_lost"):
                log.error("%s", e)
            stop.update(now=True, lease_lost=True)
            return  # the heartbeat row now belongs to the engine that holds the lease
        try:
            eng.store.heartbeat(eng.process, {**st, "host": host, "ts": datetime.now(UTC).isoformat(),
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
        step("fred_vix", lambda: round(float(eng.market.vix_loader().dropna().iloc[-1]), 2))
        print(json.dumps(report, indent=1, default=str))
        ok = not any(isinstance(v, str) and v.startswith("FAILED") for v in report.values())
        print("ALL GOOD" if ok else "Fix the FAILED lines above, then run --check again.")
        raise SystemExit(0 if ok else 1)
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    host = socket.gethostname()
    lease = EngineLease(eng.store, holder_id(settings.data_dir, host), host)
    start_with_lease(eng, lease, adopt_journal=a.adopt_journal)
    replicator = Replicator(eng.journal, eng.store.remote)  # S77: Supabase catches up in the background
    # Once, now: with 0018 the new tables; without it the pre-0018 ones (the dashboard keeps updating).
    # Supabase unreachable: asked again at the first pass that reaches it.
    log.info("replica schema: %s", replicator.detect() or "not known yet (Supabase unreachable)")
    replicator.start()
    status: dict = {"state": "starting"}
    last_minute = None
    log.info("engine up (code %s). Ctrl-C to stop; broker-held stops stay in place.", eng.code)
    last_error = ""

    beat = make_beat(eng, lease, host, stop)

    eng.on_progress = lambda st: beat(st)  # long steps (history load) report progress
    while not stop["now"]:
        try:
            if not lease.ok:  # a slow or failed step may have let it lapse: prove it is ours before acting
                lease.renew(force=True)
            eng.apply_commands()
            now = datetime.now(UTC)
            minute = now.replace(second=0, microsecond=0)
            if minute != last_minute and now.second >= 7:
                status = eng.tick()
                eng.on_tick_ok()
                last_minute = minute
                if status.get("state") != "market_closed":
                    log.info("tick %s", status)
        except LeaseLost as e:
            log.error("%s", e)
            stop.update(now=True, lease_lost=True)
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
        report_conflicts(eng, replicator)
        status = {**status, "replication": replicator.status()} if isinstance(status, dict) else status
        beat(status)
        for _ in range(10):
            if stop["now"]:
                break
            time.sleep(1)
    if stop.get("lease_lost"):
        # Another engine is in charge: write nothing. Its heartbeat and lease are not ours, and an event from
        # here would fork the hash-chained event log it is now appending to. Its `engine_lease_takeover` event
        # is the record; this side has the log line.
        log.error("engine stopped: lease lost (positions it was managing: %s)", sorted(eng.pos))
        replicator.stop(flush_s=5.0)  # our own rows only (engine-assigned ids): safe to finish sending
        raise SystemExit(EXIT_LEASE)
    try:
        eng.store.heartbeat(eng.process, {"state": "stopped", "host": host})
        eng.store.event("engine_stop", {"positions": list(eng.pos)}, eng._rid())
    except Exception:
        pass
    replicator.stop()
    lease.release()  # clean stop: another machine may start at once
    log.info("engine stopped")


if __name__ == "__main__":
    main()
