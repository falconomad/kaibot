"""A fake of Alpaca's paper trading REST API for the broker adapter contract tests (M12 ticket 4.9, #488).

It is the HTTP side only: `opener(request, timeout)` takes the place of `urllib.request.urlopen` inside the
adapter's `_Http`, so `AlpacaPaper` runs unchanged (its URLs, headers, bodies, error handling and paging) and no
packet leaves the process. It refuses any URL that is not the paper endpoint.

Not a simulator: it has no prices, no bars and no fill rules. A test tells it what executes (`execute`). What it
does model is what Alpaca's API reference documents and the adapter depends on:

- POST /v2/orders: the order object; 422 on a client_order_id the account already used, on an invalid body; 403
  when a sale has no free shares or a buy no buying power;
- GET /v2/orders (status=open, nested), GET /v2/orders/{id}, GET /v2/orders:by_client_order_id (404 when unknown);
- PATCH /v2/orders/{id}: a NEW order (new id, `replaces`), the old one turns `replaced` (`replaced_by`);
- DELETE /v2/orders/{id}: 204; 404 unknown; 422 when the order is no longer cancelable;
- GET /v2/positions, DELETE /v2/positions/{symbol} (404 without a position);
- GET /v2/account, /v2/clock, /v2/calendar, /v2/assets/{symbol};
- GET /v2/account/activities: FILL rows, ascending, `after`, `page_size`, `page_token`; 422 on an unknown type.

Numbers are strings, as Alpaca sends them. Every request is kept in `requests` (method, path, query, body).
"""

from __future__ import annotations

import io
import itertools
import json
import urllib.error
import urllib.parse
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
PAPER = "https://paper-api.alpaca.markets"
OPEN_STATUSES = ("new", "accepted", "partially_filled", "pending_cancel", "pending_replace")
ACTIVITY_TYPES = {"FILL", "DIV", "SPLIT", "FEE", "DIVNRA", "CSD", "CSW", "JNLC", "INT"}


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeAlpacaPaper:
    def __init__(self, now, cash: float = 100_000.0, sessions: tuple[str, ...] = ()):
        self.now = now  # () -> aware datetime
        self.cash = cash
        self.sessions = set(sessions)
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, dict] = {}
        self.activities: list[dict] = []
        self.requests: list[tuple[str, str, dict, dict | None]] = []
        self.ids = itertools.count(1)
        self.down = False  # True: every request fails below HTTP (connection refused)
        self.lose_next_answer = False  # True: the next POST /v2/orders is accepted and its answer is lost
        self.reference_price = 100.0  # what a market order is checked against (buying power)

    # ---------------------------------------------------------------- the transport
    def opener(self, req, timeout=None):
        url = urllib.parse.urlsplit(req.full_url)
        if f"{url.scheme}://{url.netloc}" != PAPER:
            raise AssertionError(f"a request left for {url.scheme}://{url.netloc}: only the paper endpoint is allowed")
        if not req.get_header("Apca-api-key-id") or not req.get_header("Apca-api-secret-key"):
            self._fail(401, "unauthorized.")
        if self.down:
            raise urllib.error.URLError("[Errno 111] Connection refused")
        query = dict(urllib.parse.parse_qsl(url.query))
        body = json.loads(req.data) if req.data else None
        method = req.get_method()
        self.requests.append((method, url.path, query, body))
        out = self._route(method, url.path, query, body)
        return _Response(b"" if out is None else json.dumps(out).encode())

    def _fail(self, status: int, message: str, code: int | None = None):
        body = json.dumps({"code": code or status * 100000, "message": message}).encode()
        raise urllib.error.HTTPError("https://paper-api.alpaca.markets", status, message, {}, io.BytesIO(body))

    def _route(self, method: str, path: str, q: dict, body: dict | None):
        parts = path.strip("/").split("/")
        if parts[:2] == ["v2", "orders:by_client_order_id"] and method == "GET":
            o = next((o for o in self.orders.values() if o["client_order_id"] == q.get("client_order_id")), None)
            return o if o is not None else self._fail(404, "order not found")
        if parts[:2] == ["v2", "orders"]:
            if len(parts) == 2:
                return self._submit(body) if method == "POST" else self._list_orders(q)
            oid = parts[2]
            if oid not in self.orders:
                self._fail(404, f"order not found: {oid}")
            return {"GET": lambda: self.orders[oid], "DELETE": lambda: self._cancel(oid),
                    "PATCH": lambda: self._replace(oid, body or {})}[method]()
        if parts[:2] == ["v2", "positions"]:
            if len(parts) == 2:
                return list(self.positions.values())
            return self._close(parts[2])
        if parts[:3] == ["v2", "account", "activities"]:
            return self._activities(q)
        if parts[:2] == ["v2", "account"]:
            value = sum(float(p["qty"]) * float(p["avg_entry_price"]) for p in self.positions.values())
            return {"id": "paper-account", "status": "ACTIVE", "cash": f"{self.cash:.2f}",
                    "equity": f"{self.cash + value:.2f}", "buying_power": f"{max(self.cash, 0.0):.2f}",
                    "shorting_enabled": False}
        if parts[:2] == ["v2", "clock"]:
            return self._clock()
        if parts[:2] == ["v2", "calendar"]:
            return [{"date": d, "open": "09:30", "close": "16:00"} for d in sorted(self.sessions)
                    if q.get("start", "") <= d <= q.get("end", "9999")]
        if parts[:2] == ["v2", "assets"]:
            self._fail(404, f"asset not found for {parts[2]}")
        self._fail(404, f"endpoint not found: {method} {path}")

    # ---------------------------------------------------------------- orders
    def _submit(self, b: dict | None) -> dict:
        b = b or {}
        for k in ("symbol", "qty", "side", "type", "time_in_force"):
            if k not in b:
                self._fail(422, f"{k} is required")
        if not isinstance(b["qty"], str):
            self._fail(422, "qty must be a string")  # the adapter sends numbers as Alpaca's examples do
        qty = float(b["qty"])
        if not qty > 0 or b["side"] not in ("buy", "sell") or b["type"] not in ("market", "limit", "stop",
                                                                                 "stop_limit"):
            self._fail(422, "invalid order")
        coid = b.get("client_order_id")
        if coid and any(o["client_order_id"] == coid for o in self.orders.values()):
            self._fail(422, "client_order_id must be unique", 40010001)
        if b["side"] == "sell":
            held = float((self.positions.get(b["symbol"]) or {}).get("qty", 0))
            working = sum(float(o["qty"]) - float(o["filled_qty"]) for o in self.orders.values()
                          if o["symbol"] == b["symbol"] and o["side"] == "sell" and o["status"] in OPEN_STATUSES)
            if qty > held - working + 1e-9:
                self._fail(403, f"insufficient qty available for order (requested: {qty:g}, available: "
                                f"{held - working:g})", 40310000)
        else:
            px = float(b.get("limit_price") or b.get("stop_price") or self.reference_price)
            if qty * px > self.cash + 1e-9:
                self._fail(403, "insufficient buying power", 40310000)
        o = self._new_order(b["symbol"], b["side"], b["qty"], b["type"], b["time_in_force"],
                            coid or f"alpaca-{next(self.ids)}", b.get("limit_price"), b.get("stop_price"),
                            b.get("order_class") or "simple")
        if self.lose_next_answer:
            self.lose_next_answer = False
            raise TimeoutError("The read operation timed out")  # accepted; the answer never arrives
        return o

    def _new_order(self, symbol, side, qty, kind, tif, coid, limit, stop, order_class) -> dict:
        oid = f"ord-{next(self.ids):04d}"
        now = self.now().astimezone(UTC).isoformat()
        o = {"id": oid, "client_order_id": coid, "symbol": symbol, "side": side, "qty": str(qty),
             "filled_qty": "0", "filled_avg_price": None, "type": kind, "order_type": kind, "time_in_force": tif,
             "order_class": order_class, "limit_price": limit, "stop_price": stop, "status": "new",
             "created_at": now, "submitted_at": now, "filled_at": None, "canceled_at": None, "expired_at": None,
             "replaced_at": None, "replaced_by": None, "replaces": None, "legs": None, "asset_class": "us_equity"}
        self.orders[oid] = o
        return o

    def _list_orders(self, q: dict) -> list[dict]:
        if q.get("status", "open") == "open":
            return [o for o in self.orders.values() if o["status"] in OPEN_STATUSES]
        return list(self.orders.values())

    def _cancel(self, oid: str):
        o = self.orders[oid]
        if o["status"] not in OPEN_STATUSES:
            self._fail(422, f"order is not cancelable: {o['status']}")
        o.update(status="canceled", canceled_at=self.now().astimezone(UTC).isoformat())
        return None  # 204

    def _replace(self, oid: str, b: dict) -> dict:
        o = self.orders[oid]
        if o["status"] not in ("new", "accepted", "partially_filled"):
            self._fail(422, f"order is not open: {o['status']}")
        coid = b.get("client_order_id")
        if coid and any(x["client_order_id"] == coid for x in self.orders.values()):
            self._fail(422, "client_order_id must be unique", 40010001)
        new = self._new_order(o["symbol"], o["side"], b.get("qty", o["qty"]), o["type"], o["time_in_force"],
                              coid or f"alpaca-{next(self.ids)}", b.get("limit_price", o["limit_price"]),
                              b.get("stop_price", o["stop_price"]), o["order_class"])
        new["replaces"] = oid
        o.update(status="replaced", replaced_by=new["id"], replaced_at=new["created_at"])
        return new

    def _close(self, symbol: str):
        p = self.positions.get(symbol)
        if p is None:
            self._fail(404, f"position does not exist: {symbol}")
        return self._new_order(symbol, "sell", p["qty"], "market", "day", f"alpaca-{next(self.ids)}", None, None,
                               "simple")

    # ---------------------------------------------------------------- what a test makes happen
    def execute(self, order_id: str, qty: float, price: float) -> dict:
        """The venue executes `qty` shares of the order at `price`: the order, the position, the cash and one FILL
        activity change, as Alpaca reports them."""
        o = self.orders[order_id]
        assert o["status"] in OPEN_STATUSES, f"{order_id} is {o['status']}"
        done = float(o["filled_qty"])
        total = float(o["qty"])
        assert qty <= total - done + 1e-9
        avg = price if not done else (float(o["filled_avg_price"]) * done + price * qty) / (done + qty)
        complete = done + qty >= total - 1e-9
        t = self.now().astimezone(UTC)
        o.update(filled_qty=_n(done + qty), filled_avg_price=str(avg),
                 status="filled" if complete else "partially_filled", filled_at=t.isoformat() if complete else None)
        p = self.positions.get(o["symbol"])
        if o["side"] == "buy":
            have = float(p["qty"]) if p else 0.0
            cost = float(p["avg_entry_price"]) * have if p else 0.0
            self.positions[o["symbol"]] = {"symbol": o["symbol"], "qty": _n(have + qty), "side": "long",
                                           "avg_entry_price": str((cost + price * qty) / (have + qty))}
            self.cash -= qty * price
        else:
            left = float(p["qty"]) - qty
            if left <= 1e-9:
                del self.positions[o["symbol"]]
            else:
                p["qty"] = _n(left)
            self.cash += qty * price
        a = {"id": f"{t.strftime('%Y%m%d%H%M%S')}000::{next(self.ids):08d}", "activity_type": "FILL",
             "transaction_time": t.isoformat().replace("+00:00", "Z"), "type": "fill" if complete else "partial_fill",
             "price": str(price), "qty": _n(qty), "side": o["side"], "symbol": o["symbol"],
             "leaves_qty": _n(total - done - qty), "cum_qty": _n(done + qty), "order_id": order_id,
             "order_status": o["status"]}
        self.activities.append(a)
        return a

    def expire_day_orders(self) -> None:
        for o in self.orders.values():
            if o["status"] in OPEN_STATUSES and o["time_in_force"] in ("day", "cls", "opg"):
                o.update(status="expired", expired_at=self.now().astimezone(UTC).isoformat())

    # ---------------------------------------------------------------- activities, clock
    def _activities(self, q: dict) -> list[dict]:
        types = [t for t in q.get("activity_types", "").split(",") if t]
        bad = [t for t in types if t not in ACTIVITY_TYPES]
        if bad:
            self._fail(422, f"invalid activity type: {','.join(bad)}", 40010001)
        rows = [a for a in self.activities if not types or a["activity_type"] in types]
        if q.get("direction", "desc") == "desc":
            rows = rows[::-1]
        if q.get("after"):
            after = datetime.fromisoformat(q["after"].replace("Z", "+00:00"))
            rows = [a for a in rows if datetime.fromisoformat(a["transaction_time"].replace("Z", "+00:00")) > after]
        if q.get("page_token"):
            ids = [a["id"] for a in rows]
            rows = rows[ids.index(q["page_token"]) + 1:] if q["page_token"] in ids else []
        return rows[: int(q.get("page_size", 100))]

    def _session(self, d: date) -> tuple[datetime, datetime] | None:
        if d.isoformat() not in self.sessions:
            return None
        return datetime.combine(d, time(9, 30), NY), datetime.combine(d, time(16, 0), NY)

    def _clock(self) -> dict:
        now = self.now().astimezone(NY)
        today = self._session(now.date())
        is_open = today is not None and today[0] <= now < today[1]
        d = now.date() if today is not None and now < today[0] else now.date() + timedelta(days=1)
        for _ in range(10):
            if self._session(d) is not None or (not self.sessions and d.weekday() < 5):
                break
            d += timedelta(days=1)
        nxt = self._session(d) or (datetime.combine(d, time(9, 30), NY), datetime.combine(d, time(16, 0), NY))
        close = today[1] if is_open else nxt[1]
        return {"timestamp": now.isoformat(), "is_open": is_open, "next_open": nxt[0].isoformat(),
                "next_close": close.isoformat()}


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(float(x))
