"""In-memory fakes for engine tests: a broker that fills on replay bars, a data feed, and a Store."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from kaibot.replay.data import ET, SymbolDays


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


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class FakeData:
    """Serves bars from full-day frames, never returning anything at or after the fake clock."""

    def __init__(self, frames: dict[str, pd.DataFrame], clock: Clock):
        self.frames, self.clock = frames, clock
        self.news: dict[str, int] = {}

    def bars(self, symbols, start, end, feed, timeframe="1Min", adjustment="split"):
        end = min(end, self.clock())
        out = {}
        for s in symbols:
            f = self.frames.get(s)
            if f is None:
                continue
            g = f[(f["ts"] >= start) & (f["ts"] < end)]
            if timeframe == "1Day":
                g = (
                    g.assign(day=g["ts"].dt.tz_convert(ET).dt.date)
                    .groupby("day", as_index=False)
                    .agg(
                        ts=("ts", "first"),
                        o=("o", "first"),
                        h=("h", "max"),
                        l=("l", "min"),
                        c=("c", "last"),
                        v=("v", "sum"),
                        vw=("vw", "mean"),
                    )[["ts", "o", "h", "l", "c", "v", "vw"]]
                )
            out[s] = g.reset_index(drop=True)
        return out

    def latest_quotes(self, symbols):
        now = self.clock()
        out = {}
        for s in symbols:
            f = self.frames[s]
            g = f[f["ts"] < now]
            if len(g):
                c = float(g["c"].iloc[-1])
                out[s] = {"bp": c - 0.01, "ap": c + 0.01, "t": (now - timedelta(seconds=1)).isoformat()}
        return out

    def news_count(self, symbol, start, end):
        return self.news.get(symbol, 0)


class FakeBroker:
    """Market orders fill at the next bar's open; bracket legs trigger on later bars (stop first)."""

    name = "fake"

    def __init__(self, data: FakeData, clock: Clock, day: str):
        self.data, self.clk, self.day = data, clock, day
        self.orders: dict[str, dict] = {}
        self.pos: dict[str, dict] = {}
        self.ids = itertools.count(1)
        self.foreign: dict[str, dict] = {}

    def clock(self):
        now = pd.Timestamp(self.clk()).tz_convert(ET)
        o = pd.Timestamp(f"{self.day} 09:30", tz=ET)
        c = pd.Timestamp(f"{self.day} 16:00", tz=ET)
        nxt = o + pd.Timedelta(days=1) if now >= o else o
        return {
            "is_open": bool(o <= now < c),
            "next_open": nxt.isoformat(),
            "next_close": c.isoformat(),
            "timestamp": now.isoformat(),
        }

    def _bar(self, s, t):
        f = self.data.frames[s]
        g = f[f["ts"] == t]
        return None if not len(g) else g.iloc[0]

    def advance(self, t: datetime) -> None:
        """Process the bar that started at minute t (fills happen at its open, triggers within it)."""
        for o in list(self.orders.values()):
            b = self._bar(o["symbol"], t)
            if b is None:
                continue
            if o["status"] == "new" and o["type"] == "market":
                px = float(b["o"])
                o.update(status="filled", filled_avg_price=str(px), filled_at=t.isoformat())
                if o["side"] == "buy":
                    self.pos[o["symbol"]] = {"qty": o["qty"], "avg_entry_price": px}
                    for leg in o["legs"]:
                        leg["status"] = "new"
                else:
                    self.pos.pop(o["symbol"], None)
                continue
            if o["status"] == "filled" and o.get("legs"):
                stop = next(x for x in o["legs"] if x["type"] == "stop")
                tgt = next(x for x in o["legs"] if x["type"] == "limit")
                if stop["status"] != "new":
                    continue
                if b["l"] <= float(stop["stop_price"]):
                    px = min(float(stop["stop_price"]), float(b["o"]))
                    stop.update(status="filled", filled_avg_price=str(px), filled_at=t.isoformat())
                    tgt["status"] = "canceled"
                    self.pos.pop(o["symbol"], None)
                elif b["h"] >= float(tgt["limit_price"]) + 0.01:
                    tgt.update(status="filled", filled_avg_price=tgt["limit_price"], filled_at=t.isoformat())
                    stop["status"] = "canceled"
                    self.pos.pop(o["symbol"], None)

    # --- broker API used by the engine ---
    def account(self):
        a = {"status": "ACTIVE", "equity": "100000"}
        if getattr(self, "buying_power", None) is not None:
            a["buying_power"] = str(self.buying_power)
        return a

    def calendar(self, day):
        return {"date": day, "open": "09:30", "close": "16:00"}

    def positions(self):
        return {**self.foreign, **self.pos}

    def open_orders(self):
        return [o for o in self.orders.values() if o["status"] == "new"]

    def order(self, oid):
        return self.orders[oid]

    def order_by_client_id(self, coid):
        return next((o for o in self.orders.values() if o.get("client_order_id") == coid), None)

    def submit_bracket(self, coid, symbol, qty, stop, target):
        oid = f"o{next(self.ids)}"
        o = {
            "id": oid,
            "client_order_id": coid,
            "symbol": symbol,
            "qty": qty,
            "side": "buy",
            "type": "market",
            "status": "new",
            "legs": [
                {"id": f"{oid}s", "type": "stop", "stop_price": f"{stop:.2f}", "status": "held"},
                {"id": f"{oid}t", "type": "limit", "limit_price": f"{target:.2f}", "status": "held"},
            ],
        }
        self.orders[oid] = o
        return o

    def _leg(self, leg_id):
        for o in self.orders.values():
            for leg in o.get("legs") or []:
                if leg["id"] == leg_id:
                    return leg
        raise KeyError(leg_id)

    def replace_stop(self, leg_id, stop):
        leg = self._leg(leg_id)
        leg["stop_price"] = f"{stop:.2f}"
        return leg

    def replace_limit(self, leg_id, limit):
        leg = self._leg(leg_id)
        leg["limit_price"] = f"{limit:.2f}"
        return leg

    def cancel_order(self, oid):
        if oid in self.orders and self.orders[oid]["status"] == "new":
            self.orders[oid]["status"] = "canceled"

    def close_position(self, symbol):
        if symbol not in self.pos:
            return None
        for o in self.orders.values():
            if o["symbol"] == symbol:
                for leg in o.get("legs") or []:
                    if leg["status"] in ("new", "held"):
                        leg["status"] = "canceled"
        oid = f"o{next(self.ids)}"
        o = {
            "id": oid,
            "symbol": symbol,
            "qty": self.pos[symbol]["qty"],
            "side": "sell",
            "type": "market",
            "status": "new",
            "legs": [],
        }
        self.orders[oid] = o
        return o


class FakeStore:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.ids = itertools.count(1)
        self.beats: dict = {}

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
