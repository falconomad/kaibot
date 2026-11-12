"""Alpaca paper trading over plain HTTPS (stdlib only, rate-limited, retried): the `Broker` for the paper account.

Paper only: the trading base URL is fixed to the paper endpoint (config.PAPER_TRADING_URL). `_Http` is shared
with the market-data client in kaibot.engine.alpaca.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from kaibot.broker.protocol import BrokerBase, BrokerError, OrderRequest
from kaibot.common.ratelimit import Budget, get_budget
from kaibot.engine.config import PAPER_TRADING_URL


class AlpacaError(BrokerError):
    def __init__(self, status: int, body: str):
        super().__init__(status, body, f"Alpaca {status}: {body[:300]}")


# S74c (#147): every call of the trading client (submit, cancel, replace, close, positions, open orders, clock)
# ends within ORDER_DEADLINE_S, retries, back-off and the budget wait included, well inside the 90 s heartbeat
# window (watchdog.STALE_S). Each attempt waits at most ORDER_TIMEOUT_S for the venue; Alpaca answers in well
# under a second, so a 6 s silence is a stuck connection. Worst case: 6 + 1 + 6 + 2 + 5 = 20 s, three attempts.
# The market-data client keeps the long retries (30 s a try, 5 tries) and no deadline: it is not on the order path.
ORDER_TIMEOUT_S, ORDER_DEADLINE_S = 6.0, 20.0
MIN_ATTEMPT_S = 1.0  # no attempt is started with less than this left


class _Http:
    """Paced by the process-wide budget of `source` (config/sources.yaml), shared by every client of that source.
    A 429 holds the whole source for the backoff; 5xx and network errors back off this caller only.
    With `deadline_s` the whole call, budget waits, attempts and back-off included, ends by then: an attempt's
    timeout is cut to what is left, and no wait is started that would end past it (AlpacaError 0, "deadline")."""

    def __init__(self, key: str, secret: str, source: str = "alpaca", budget: Budget | None = None,
                 opener=None, sleep=None, timeout_s: float = 30.0, deadline_s: float | None = None):
        self.h = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "User-Agent": "kaibot-engine/0.2",
            "Content-Type": "application/json",
        }
        self.budget = budget or get_budget(source)
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep or self.budget.clock.sleep
        self.timeout_s, self.deadline_s = timeout_s, deadline_s

    def _left(self, end: float | None) -> float:
        return float("inf") if end is None else end - self.budget.clock.monotonic()

    def _acquire(self, end: float | None) -> bool:
        """The budget's slot; with a deadline, False instead of waiting for a slot that comes too late. That slot
        is then not booked at all, so it does not push the other callers back (S74d, #154)."""
        if end is None:
            self.budget.acquire()
            return True
        t = self.budget.reserve(latest=end - MIN_ATTEMPT_S)
        if t is None:
            return False
        wait = t - self.budget.clock.monotonic()
        if wait > 0:
            self.budget.waited += wait
            self.budget.clock.sleep(wait)
        return True

    def call(self, method: str, url: str, params: dict | None = None, body: dict | None = None):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode() if body is not None else None
        end = None if self.deadline_s is None else self.budget.clock.monotonic() + self.deadline_s
        for attempt in range(5):
            if self._left(end) < MIN_ATTEMPT_S or not self._acquire(end):
                break
            delay = min(2**attempt, 20)
            try:
                req = urllib.request.Request(url, data=data, headers=self.h, method=method)
                with self._open(req, timeout=min(self.timeout_s, self._left(end))) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code < 500 and e.code != 429:
                    raise AlpacaError(e.code, text) from None
                if self._left(end) - delay < MIN_ATTEMPT_S:
                    break
                if e.code == 429:
                    self.budget.backoff(delay)
                else:
                    self._sleep(delay)
            except (urllib.error.URLError, TimeoutError):
                if self._left(end) - delay < MIN_ATTEMPT_S:
                    break
                self._sleep(delay)
        else:
            raise AlpacaError(0, f"giving up on {method} {url}")
        raise AlpacaError(0, f"giving up on {method} {url}: deadline {self.deadline_s:g} s")


ACTIVITY_PAGE, ACTIVITY_MAX_PAGES = 100, 50
# S74 (#114, D7): close_position waits at most CANCEL_POLLS x CANCEL_POLL_S for its cancels to be confirmed.
CANCEL_POLLS, CANCEL_POLL_S = 10, 0.5
CANCEL_WAIT_S = CANCEL_POLLS * CANCEL_POLL_S


class AlpacaPaper(BrokerBase):
    """Trading endpoints (paper account): the `Broker` protocol over Alpaca's REST API."""

    name = "alpaca_paper"

    def __init__(self, key: str, secret: str):
        self.http = _Http(key, secret, source="alpaca_trading", timeout_s=ORDER_TIMEOUT_S, deadline_s=ORDER_DEADLINE_S)
        self.base = PAPER_TRADING_URL

    def _t(self, method, path, params=None, body=None):
        return self.http.call(method, f"{self.base}{path}", params, body)

    def account(self) -> dict:
        return self._t("GET", "/v2/account")

    def activities(self, after: datetime | None = None, types: tuple[str, ...] = ("FILL",)) -> list[dict]:
        """Account activities, oldest first (GET /v2/account/activities, 100 a page). Fills: the order manager
        (S78) reconciles from them. Dividends and splits (DIV*, SSP): the ledger books them (S77)."""
        out: list[dict] = []
        params = {"activity_types": ",".join(types), "direction": "asc", "page_size": ACTIVITY_PAGE}
        if after is not None:
            params["after"] = after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for _ in range(ACTIVITY_MAX_PAGES):
            rows = self._t("GET", "/v2/account/activities", dict(params)) or []
            out.extend(rows)
            if len(rows) < ACTIVITY_PAGE:
                break
            params["page_token"] = rows[-1]["id"]
        return out

    def clock(self) -> dict:
        return self._t("GET", "/v2/clock")

    def calendar(self, day: str) -> dict | None:
        rows = self._t("GET", "/v2/calendar", {"start": day, "end": day})
        return rows[0] if rows else None

    def positions(self) -> dict[str, dict]:
        return {p["symbol"]: p for p in self._t("GET", "/v2/positions")}

    def open_orders(self) -> list[dict]:
        return self._t("GET", "/v2/orders", {"status": "open", "nested": "true", "limit": 500})

    def order(self, order_id: str) -> dict:
        return self._t("GET", f"/v2/orders/{order_id}", {"nested": "true"})

    def order_by_client_id(self, coid: str) -> dict | None:
        try:
            return self._t("GET", "/v2/orders:by_client_order_id", {"client_order_id": coid, "nested": "true"})
        except AlpacaError as e:
            if e.status == 404:
                return None
            raise

    def submit(self, order: OrderRequest) -> dict:
        """Place the order. Adopts the existing order on a duplicate client_order_id, so a retried or repeated
        submission (lost response, restart) never places a second one."""
        try:
            return self._t("POST", "/v2/orders", body=order.body())
        except AlpacaError as e:
            if e.status == 422 and "client_order_id" in e.body:
                existing = self.order_by_client_id(order.client_order_id)
                if existing:
                    return existing
            raise

    def replace(self, order_id: str, *, stop_price: float | None = None, limit_price: float | None = None,
                qty: float | None = None) -> dict:
        """PATCH /v2/orders/{id}: Alpaca replaces the order and answers with the NEW order (new id, `replaces` the
        old one, which turns `replaced`). `qty` is sent only when given (the order manager resizes a protective
        stop with it), so the engine's present calls send the same bytes as before."""
        body = {}
        if qty is not None:
            body["qty"] = str(int(qty)) if float(qty).is_integer() else str(qty)
        if limit_price is not None:
            body["limit_price"] = f"{limit_price:.2f}"
        if stop_price is not None:
            body["stop_price"] = f"{stop_price:.2f}"
        return self._t("PATCH", f"/v2/orders/{order_id}", body=body)

    def cancel(self, order_id: str) -> None:
        try:
            self._t("DELETE", f"/v2/orders/{order_id}")
        except AlpacaError as e:
            if e.status not in (404, 422):
                raise

    def close_position(self, symbol: str, polls: int = CANCEL_POLLS, poll_s: float = CANCEL_POLL_S) -> dict | None:
        """Cancel the symbol's open orders, wait until they are gone, then sell the whole position at market.

        A cancel is only a request (204 = accepted): until the venue confirms it the order still holds the
        shares, and the close is refused (403). So after asking, read the open orders again every `poll_s`
        seconds, at most `polls` times (CANCEL_WAIT_S, 5 s), and close once none is left for the symbol. If some
        still are, the position is NOT deleted: AlpacaError(409) is raised, a refusal that Engine.flatten_all
        reports and the next loop pass retries. An order already `pending_cancel` (asked on an earlier attempt)
        is only waited for, not asked again. With nothing to cancel the calls are the same two as before
        (S74, #114, audit D7). The extra arguments are optional: the `Broker` protocol call is close_position(s)."""
        asked: set[str] = set()
        for left in range(polls, -1, -1):
            mine = [o for o in self.open_orders() if o["symbol"] == symbol]
            for o in mine:
                if o["id"] not in asked and o.get("status") != "pending_cancel":
                    asked.add(o["id"])  # each cancel is sent once; the later reads only wait for it
                    self.cancel_order(o["id"])
            if not mine:
                break
            if not left:
                ids = ", ".join(o["id"] for o in mine)
                raise AlpacaError(409, f"close {symbol}: cancel not confirmed after {polls * poll_s:g} s ({ids})")
            self.http._sleep(poll_s)
        try:
            return self._t("DELETE", f"/v2/positions/{symbol}")
        except AlpacaError as e:
            if e.status == 404:
                return None
            raise
