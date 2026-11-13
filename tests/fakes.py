"""In-memory fakes for engine tests: a Store and an LLM, plus thin test flavours of the simulated broker, feed
and clock from src (kaibot.broker.sim.SimBroker, kaibot.replay.history.HistoricalData, kaibot.engine.clock)."""

from __future__ import annotations

import itertools
import threading
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from kaibot.broker.sim import REGULAR_HOURS, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.replay.data import ET, SymbolDays
from kaibot.replay.history import HistoricalData


def frame_from_days(sd: SymbolDays) -> pd.DataFrame:
    rows = []
    for di, d in enumerate(sd.dates):
        open_utc = pd.Timestamp(str(d) + " 09:30", tz=ET).tz_convert(UTC)
        ts = open_utc + pd.to_timedelta(np.arange(390), unit="m")
        rows.append(
            pd.DataFrame(
                {"ts": ts, "o": sd.o[di], "h": sd.h[di], "l": sd.l[di], "c": sd.c[di], "v": sd.v[di], "vw": sd.vw[di]}
            )
        )
    return pd.concat(rows, ignore_index=True)


class Clock(SimClock):
    """Simulated time for tests; `clock.t = ...` sets it directly."""


class FakeData(HistoricalData):
    """`HistoricalData` (bars from full-day frames, never anything at or after the fake clock) with the quotes
    and news the engine tests were written against: a two-cent quote one second old, and a headline count per
    symbol that the test sets."""

    def __init__(self, frames: dict[str, pd.DataFrame], clock: Clock):
        super().__init__(frames, clock)
        self.news: dict[str, int] = {}

    def latest_quotes(self, symbols):
        now = self.clock()
        out = {}
        for s in symbols:
            self.frames[s]  # an unknown symbol fails the whole read, as before
            c = self.history.last_close(s, now)
            if c is not None:
                out[s] = {"bp": c - 0.01, "ap": c + 0.01, "t": (now - timedelta(seconds=1)).isoformat()}
        return out

    def news_count(self, symbol, start, end):
        return self.news.get(symbol, 0)


class FakeBroker(SimBroker):
    """`SimBroker` as the engine tests use it: fills at raw bar prices (no cost model), no cash book, one
    session (`day`, 09:30-16:00) and a duplicate client_order_id raises, so a double submission in a test fails
    loudly. Market orders fill at the next bar's open; bracket legs trigger on later bars (stop first)."""

    name = "fake"
    strict_time = False  # the engine tests step back in time (run_day ends after the close, then goes on mid-day)

    def __init__(self, data: FakeData, clock: Clock, day: str):
        super().__init__(data.history, clock, costs=None, cash=None)
        self.data, self.day = data, day
        self.foreign: dict[str, dict] = {}
        self.assets: dict[str, dict | None] = {}  # S68: overrides for asset(); default = listed and tradable

    def asset(self, symbol):
        if symbol in self.assets:
            return self.assets[symbol]
        if symbol not in self.data.frames:
            return None
        return {"symbol": symbol, "class": "us_equity", "exchange": "NASDAQ", "status": "active", "tradable": True,
                "name": f"{symbol} Inc"}

    def _days(self):
        return [self.day]

    def _hours(self, day):
        return REGULAR_HOURS  # every day a test asks about is a regular session

    def clock(self):
        now = pd.Timestamp(self.clk()).tz_convert(ET)
        self._expire(now.to_pydatetime())
        o = pd.Timestamp(f"{self.day} 09:30", tz=ET)
        c = pd.Timestamp(f"{self.day} 16:00", tz=ET)
        nxt = o + pd.Timedelta(days=1) if now >= o else o
        return {
            "is_open": bool(o <= now < c),
            "next_open": nxt.isoformat(),
            "next_close": c.isoformat(),
            "timestamp": now.isoformat(),
        }

    def account(self):
        a = {"status": "ACTIVE", "equity": "100000", **self._shorting()}
        if getattr(self, "buying_power", None) is not None:
            a["buying_power"] = str(self.buying_power)
        return a

    def positions(self):
        return {**self.foreign, **self.pos}

    def _duplicate(self, existing, order):
        """Alpaca refuses a reused client_order_id (422; the adapter then returns the existing order). Raising
        here makes any duplicate submission in a test fail loudly."""
        raise ValueError(f"422 client_order_id must be unique: {order.client_order_id}")


class FakeStore:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.ids = itertools.count(1)
        self.beats: dict = {}
        self.llm = {"day": 0.0, "month": 0.0, "open": {}}  # mirrors kaibot.llm_reserve / llm_settle
        self.lease: dict | None = None  # the single kaibot.engine_lease row
        self.lease_now = lambda: datetime.now(UTC)
        self.lease_fail = False
        self.lease_calls = 0
        self._lease_lock = threading.Lock()

    def rpc(self, fn, args):
        b = self.llm
        if fn == "llm_reserve":
            open_res = sum(b["open"].values())
            r = float(args["p_reserved"])
            if b["day"] + open_res + r > args["p_daily_soft"] or b["month"] + open_res + r > args["p_monthly_hard"]:
                return None
            cid = next(self.ids)
            b["open"][cid] = r
            self.insert("llm_call", {"id": cid, "model": args["p_model"], "purpose": args["p_purpose"],
                                     "reserved_usd": r})
            return cid
        if fn == "llm_settle":
            if b["open"].pop(args["p_id"], None) is None:
                return None
            b["day"] += float(args["p_actual"])
            b["month"] += float(args["p_actual"])
            self.update("llm_call", {"id": args["p_id"]},
                        {"actual_usd": args["p_actual"], "error": args.get("p_error")})
            return None
        if fn == "llm_budget_state":
            st = ("no_entries" if b["month"] >= args["p_monthly_hard"]
                  else "rules_only" if b["day"] >= args["p_daily_soft"] else "ok")
            return {"day": b["day"], "month": b["month"], "state": st, "alert": b["month"] >= args["p_monthly_alert"]}
        raise KeyError(fn)

    # --- engine lease (issue #81): mirrors kaibot.engine_lease_* in migration 0013 ---
    # One row, compare-and-set under a lock (Postgres: the row lock). `lease_now` is the database clock;
    # `lease_fail = True` makes every lease call raise, like Supabase being unreachable.
    def _lease_guard(self):
        self.lease_calls += 1
        if self.lease_fail:
            raise ConnectionError("supabase unreachable (lease)")

    def lease_acquire(self, holder, host, ttl_s):
        self._lease_guard()
        if not holder or not 30 <= ttl_s <= 600:
            raise ValueError("engine_lease_acquire: holder required, ttl must be 30..600 s")
        with self._lease_lock:
            now, cur = self.lease_now(), self.lease
            if cur and cur["holder"] != holder and cur["expires_at"] > now:
                return {"acquired": False, "holder": cur["holder"], "host": cur["host"],
                        "started_at": cur["started_at"], "expires_at": cur["expires_at"],
                        "expires_in_s": int(-(-(cur["expires_at"] - now).total_seconds() // 1))}
            same = bool(cur) and cur["holder"] == holder
            started = cur["started_at"] if same and cur["expires_at"] > now else now
            self.lease = {"holder": holder, "host": host, "started_at": started, "renewed_at": now,
                          "expires_at": now + timedelta(seconds=ttl_s)}
            return {"acquired": True, "holder": holder, "host": host, "started_at": started,
                    "expires_at": self.lease["expires_at"], "taken_over": bool(cur) and not same,
                    "previous_holder": None if not cur or same else cur["holder"],
                    "previous_host": None if not cur or same else cur["host"]}

    def lease_renew(self, holder, ttl_s):
        self._lease_guard()
        with self._lease_lock:
            if not self.lease or self.lease["holder"] != holder:
                return False
            now = self.lease_now()
            self.lease.update(renewed_at=now, expires_at=now + timedelta(seconds=ttl_s))
            return True

    def lease_release(self, holder):
        self._lease_guard()
        with self._lease_lock:
            if not self.lease or self.lease["holder"] != holder:
                return False
            self.lease = None
            return True

    def lease_row(self):
        return dict(self.lease) if self.lease else None

    def insert(self, table, row, returning=True):
        rows = row if isinstance(row, list) else [row]
        out = []
        for r in rows:
            r = dict(r)
            r.setdefault("id", next(self.ids))
            if table == "run":
                r.setdefault("kind", "paper")
            self.tables.setdefault(table, []).append(r)
            out.append(r)
        return out[0] if isinstance(row, dict) else out

    def upsert(self, table, row, on_conflict):
        keys = on_conflict.split(",")
        t = self.tables.setdefault(table, [])
        for r in t:
            if all(str(r.get(k)) == str(row.get(k)) for k in keys):
                r.update(row)
                return
        t.append(dict(row))

    def update(self, table, match, values):
        for r in self.tables.get(table, []):
            if all(r.get(k) == v for k, v in match.items()):
                r.update(values)

    def select(self, table, params):
        rows = list(self.tables.get(table, []))
        for k, v in params.items():
            if k in ("order", "limit", "select"):
                continue
            op, _, val = v.partition(".")
            if op == "eq":
                rows = [r for r in rows if str(r.get(k)) == val]
            elif op == "lte":
                rows = [r for r in rows if str(r.get(k)) <= val]
            elif op == "is" and val == "null":
                rows = [r for r in rows if r.get(k) is None]
            elif op == "in":
                opts = val.strip("()").split(",")
                rows = [r for r in rows if str(r.get(k)) in opts]
        if params.get("order", "").startswith("from_session.desc"):
            rows.sort(key=lambda r: (r["from_session"], r["id"]), reverse=True)
        if params.get("order", "").startswith("id.desc"):
            rows.sort(key=lambda r: r["id"], reverse=True)
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return rows

    def heartbeat(self, process, detail):
        self.beats[process] = detail

    def event(self, kind, detail, run_id=None):
        self.insert("event", {"type": kind, "payload": detail, "run_id": run_id}, returning=False)

    def pending_commands(self):
        return [c for c in self.tables.get("command", []) if c.get("applied_at") is None]

    def mark_applied(self, cmd_id, payload_update=None):
        self.update(
            "command", {"id": cmd_id}, {"applied_at": "now", **({"payload": payload_update} if payload_update else {})}
        )

    def active_run(self):
        rows = [r for r in self.tables.get("run", []) if r["status"] in ("scheduled", "running", "paused")]
        return rows[-1] if rows else None

    def open_trades(self, run_id):
        return [t for t in self.tables.get("trade", []) if t["run_id"] == run_id and t.get("exit_ts") is None]

    def run_realized(self, run_id):
        return sum(
            min(r["pnl_venue"], r["pnl_shadow"]) for r in self.tables.get("daily_result", []) if r["run_id"] == run_id
        )


class FakeLLM:
    """Stands in for anthropic.messages.create: returns a scripted tool input per tool name."""

    def __init__(self, answers: dict, usage=(800, 150, 0, 0), delay=0.0, fail: set | None = None):
        self.answers, self.usage, self.delay, self.fail = answers, usage, delay, fail or set()
        self.calls: list[dict] = []

    def create(self, **kw):
        import time as _t
        from types import SimpleNamespace as NS

        self.calls.append(kw)
        name = kw["tool_choice"]["name"]
        if self.delay:
            _t.sleep(self.delay)
        if name in self.fail:
            raise TimeoutError(f"{name} timed out")
        ans = self.answers[name]
        data = ans(kw) if callable(ans) else ans
        u = NS(input_tokens=self.usage[0], output_tokens=self.usage[1], cache_read_input_tokens=self.usage[2],
               cache_creation_input_tokens=self.usage[3])
        return NS(content=[NS(type="tool_use", name=name, input=data)], usage=u)
