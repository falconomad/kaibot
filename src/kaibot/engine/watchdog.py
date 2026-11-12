"""Watchdog (story S16): a separate process with its own Alpaca client that fails the account closed when the
engine stops beating. See docs/decisions/08_tech_design_v2.md section 3.

While the market is open, every 15 s:
- engine heartbeat older than 90 s -> cancel every unfilled Kaibot ENTRY order (client_order_id "kb-..."),
  record a halt and an event. Broker-held stops and targets are left alone: they protect what is open.
- still stale at 15:55 ET (5 minutes before the close, early closes included) -> close every position, so
  nothing is held overnight. The day counts as flattened only when every close was accepted; otherwise the
  next poll tries again, until the dead-man time (15:56 ET, DEADMAN_BEFORE_CLOSE) when the off-box dead-man
  takes over (S74c, #147).
It never opens anything. If the heartbeat cannot be read (Supabase down) it counts as stale: failing closed
costs at most a flat afternoon, failing open could cost an overnight gap.

Limits: it runs on the same machine as the engine, so it does not survive the laptop sleeping or losing power.
For that, the broker-held GTC stops protect open positions, and the off-box dead-man (migration 0006, Supabase
pg_cron at 15:56 ET) flattens the account if the heartbeat is stale.

python -m kaibot.engine.watchdog            (started by scripts/run_engine.sh next to the engine)
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime, timedelta

from kaibot.broker.protocol import Broker

log = logging.getLogger("kaibot.watchdog")

STALE_S = 90
FLATTEN_BEFORE_CLOSE = timedelta(minutes=5)
DEADMAN_BEFORE_CLOSE = timedelta(minutes=4)  # the pg_cron dead-man at 15:56 ET (migration 0006)
POLL_S = 15
ENTRY_PREFIX = "kb-"
OPEN_ENTRY_STATES = {"new", "accepted", "pending_new", "partially_filled", "held"}


class Watchdog:
    def __init__(self, broker: Broker, store, engine_process: str = "engine", stale_s: float = STALE_S,
                 now=lambda: datetime.now(UTC)):
        self.broker, self.store, self.engine_process = broker, store, engine_process
        self.stale_s, self.now = stale_s, now
        self.tripped = False
        self.flattened_on = None
        self._flatten_tries: tuple = (None, 0)  # (day, flatten attempts that day)

    def heartbeat_age(self) -> float:
        try:
            rows = self.store.select("heartbeat", {"process": f"eq.{self.engine_process}", "limit": "1"})
        except Exception:
            log.exception("heartbeat read failed: treating the engine as down")
            return float("inf")
        if not rows or not rows[0].get("ts"):
            return float("inf")
        ts = rows[0]["ts"]
        ts = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return max(0.0, (self.now() - ts).total_seconds())

    def _entry_orders(self) -> list[dict]:
        return [o for o in self.broker.open_orders()
                if o.get("side") == "buy" and str(o.get("client_order_id") or "").startswith(ENTRY_PREFIX)
                and o.get("status") in OPEN_ENTRY_STATES and not o.get("filled_at")]

    def step(self) -> dict:
        clk = self.broker.clock()
        if not clk.get("is_open"):
            self.tripped = False
            self._beat({"state": "waiting", "next_open": clk.get("next_open")})  # visibly alive before the open
            return {"state": "market_closed"}
        age = self.heartbeat_age()
        self._beat({"engine_heartbeat_age_s": None if age == float("inf") else round(age, 1)})
        if age <= self.stale_s:
            if self.tripped:
                self._event("watchdog_recovered", {"heartbeat_age_s": round(age, 1)})
            self.tripped = False
            return {"state": "ok", "age_s": age}
        out = {"state": "engine_stale", "age_s": age, "canceled": [], "closed": []}
        for o in self._entry_orders():
            try:
                self.broker.cancel_order(o["id"])
                out["canceled"].append(o["symbol"])
            except Exception:
                log.exception("cancel failed for %s", o.get("symbol"))
        if not self.tripped:
            self.tripped = True
            log.error("engine heartbeat %.0f s old: entries canceled %s", age, out["canceled"])
            self._halt("watchdog", f"engine heartbeat {age:.0f} s old; entry orders canceled")
        close = datetime.fromisoformat(str(clk["next_close"]).replace("Z", "+00:00"))
        now, today = self.now(), self.now().date()
        tries = self._flatten_tries[1] if self._flatten_tries[0] == today else 0
        if (now >= close - FLATTEN_BEFORE_CLOSE and self.flattened_on != today
                and (now < close - DEADMAN_BEFORE_CLOSE or not tries)):  # after the dead-man time: one try at most
            self._flatten_tries = (today, tries + 1)
            self._flatten(today, tries + 1, out)
        return out

    def _flatten(self, today, attempt: int, out: dict) -> None:
        """Close every position. Done for the day only if every close was accepted (S74c, #147)."""
        failed: dict[str, str] = {}
        try:
            symbols = list(self.broker.positions())
        except Exception as e:
            log.exception("positions read failed")
            symbols, failed["positions"] = [], f"{type(e).__name__}: {e}"[:200]
        for sym in symbols:
            try:
                self.broker.close_position(sym)
                out["closed"].append(sym)
            except Exception as e:
                log.exception("close failed for %s", sym)
                failed[sym] = f"{type(e).__name__}: {e}"[:200]
        out["flatten_failed"] = failed
        if failed:
            log.error("15:55 with the engine down, flatten attempt %d incomplete: closed %s, failed %s; "
                      "retrying until the dead-man time", attempt, out["closed"], list(failed))
        else:
            self.flattened_on = today
            log.error("15:55 with the engine down: closed %s", out["closed"])
        self._event("watchdog_flatten", {"closed": out["closed"], "failed": failed, "complete": not failed,
                                         "attempt": attempt})

    def _beat(self, detail: dict) -> None:
        try:
            self.store.heartbeat("watchdog", {"state": "watching", **detail})  # detail may override the state
        except Exception:
            log.exception("watchdog heartbeat failed")

    def _event(self, kind: str, detail: dict) -> None:
        try:
            self.store.event(kind, detail)
        except Exception:
            log.exception("event write failed")

    def _halt(self, kind: str, reason: str) -> None:
        try:
            self.store.insert("halt", {"run_id": None, "kind": kind, "reason": reason}, returning=False)
        except Exception:
            log.exception("halt write failed")
        self._event("halt", {"kind": kind, "reason": reason})


def main() -> None:
    from kaibot.broker.alpaca import AlpacaPaper
    from kaibot.engine.config import Settings
    from kaibot.engine.store import Store

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s watchdog %(message)s")
    settings = Settings.from_env()
    wd = Watchdog(AlpacaPaper(settings.alpaca_key, settings.alpaca_secret),
                  Store(settings.supabase_url, settings.supabase_service_key), settings.engine_name)
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    log.info("watchdog up: watching heartbeat of %r", settings.engine_name)
    while not stop["now"]:
        try:
            st = wd.step()
            if st["state"] not in ("ok", "market_closed"):
                log.info("%s", st)
        except Exception:
            log.exception("watchdog step failed (will retry)")
        for _ in range(POLL_S):
            if stop["now"]:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
