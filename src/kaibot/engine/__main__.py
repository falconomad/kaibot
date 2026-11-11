"""Run the engine: `python -m kaibot.engine` (or the Docker image). Settings come from the environment / .env.

Loop: every ~10 s apply dashboard commands and send a heartbeat; once per completed minute (at :07) run a tick.
Stops cleanly on Ctrl-C / SIGTERM. Open positions keep their broker-held stops either way.
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
from kaibot.engine.events import load_calendar
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
    store = Store(settings.supabase_url, settings.supabase_service_key)
    events, covers_until = load_calendar(ROOT / "config" / "macro_calendar.yaml")
    fomc = {np.datetime64(d, "D") for d, evs in events.items() if any(e == "fomc_statement_1400" for e, _ in evs)}
    gw = gateway_from_env(store, os.environ)  # S32/S34: the room runs only with ANTHROPIC_API_KEY and KAIBOT_ROOM=on
    room = Room(gw) if gw.enabled else None
    log.info("agent room: %s", "ON (Sonnet, capped budget)" if room else "off (rules-only)")
    # S33: one news desk per engine; it triages through the gateway only when the room is on.
    desk = NewsDesk(store, source=data, gateway=gw if gw.enabled else None)
    try:
        log.info("news window: %d items reloaded", desk.load_window())
    except Exception:
        log.exception("news window reload failed: starting empty")
    configure_news(desk)
    locked = LockedDesk(desk)
    return Engine(broker, market, store, load_profiles(), fomc, process=settings.engine_name,
                  calendar_until=covers_until, events=events, strategies=load_strategies(), room=room,
                  pack_fn=make_pack_fn(locked), rules_extra=make_rules_extra(locked))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify keys and connectivity, then exit")
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

        def supa():
            eng.store.heartbeat("engine-check", {"state": "check", "host": socket.gethostname()})
            if not eng.store.select("heartbeat", {"process": "eq.engine-check"}):
                raise RuntimeError("write/read did not round-trip")
            run = eng.store.active_run()
            return {"write_read": "ok", "active_run": run and {"id": run["id"], "status": run["status"]}}

        step("supabase", supa)
        step("fred_vix", lambda: round(float(eng.market.vix_loader().dropna().iloc[-1]), 2))
        print(json.dumps(report, indent=1, default=str))
        ok = not any(isinstance(v, str) and v.startswith("FAILED") for v in report.values())
        print("ALL GOOD" if ok else "Fix the FAILED lines above, then run --check again.")
        raise SystemExit(0 if ok else 1)
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    eng.startup()
    host = socket.gethostname()
    status: dict = {"state": "starting"}
    last_minute = None
    log.info("engine up (code %s). Ctrl-C to stop; broker-held stops stay in place.", eng.code)
    last_error = ""

    def beat(st: dict) -> None:
        try:
            eng.store.heartbeat(eng.process, {**st, "host": host, "ts": datetime.now(UTC).isoformat()})
        except Exception:
            log.exception("heartbeat failed")

    eng.on_progress = lambda st: beat(st)  # long steps (history load) report progress
    while not stop["now"]:
        try:
            eng.apply_commands()
            now = datetime.now(UTC)
            minute = now.replace(second=0, microsecond=0)
            if minute != last_minute and now.second >= 7:
                status = eng.tick()
                eng.on_tick_ok()
                last_minute = minute
                if status.get("state") != "market_closed":
                    log.info("tick %s", status)
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
        beat(status)
        for _ in range(10):
            if stop["now"]:
                break
            time.sleep(1)
    try:
        eng.store.heartbeat(eng.process, {"state": "stopped", "host": host})
        eng.store.event("engine_stop", {"positions": list(eng.pos)}, eng._rid())
    except Exception:
        pass
    log.info("engine stopped")


if __name__ == "__main__":
    main()
