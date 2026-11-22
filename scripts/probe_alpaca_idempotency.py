#!/usr/bin/env python3
"""Outside-in probe of Alpaca PAPER: what happens to a repeated client_order_id, and how soon after an order is
accepted the lookups see it (M9 ticket 1.11, issue #436; the engine audit's finding M10 is "suspected" because
nobody measured this).

    PYTHONPATH=src python3 scripts/probe_alpaca_idempotency.py                    # dry run: prints the plan
    PYTHONPATH=src python3 scripts/probe_alpaca_idempotency.py --yes --write      # runs it, writes the result

What it does, on the paper account only (the paper base URL is fixed in the code, as in the engine):

1. reads the account: refuses to go on if the probe's stock is held or has an open order, or in the engine's send
   window (15:45-16:00 ET);
2. places ONE order: a DAY limit buy of 1 share at half the stock's last bid (it cannot fill), under a client id
   `probe-idem-<time>` (not a `kb...` id: the engine, the watchdog and the dead-man do not take it for theirs);
3. measures how long after the answer the order is visible to GET by client order id, GET by order id and the open
   orders list (polled for `--watch` seconds);
4. sends the same client id again, with the same body and with another quantity: records the status and the body
   of each answer and checks that still exactly one order is open;
5. cancels the order, measures how long until its status is `canceled`, reads it by client id once more, and
   sends the client id a last time (does Alpaca refuse an id that belonged to a cancelled order?). An order that
   attempt creates is cancelled at once;
6. always, also after an error or Ctrl-C: cancels every open order whose client id starts with this run's id and
   checks that none is left and that no position appeared.

The result is printed as JSON and, with --write, saved as docs/ops/alpaca_idempotency_probe.md (commit that file).
It contains no key and no account number. Run it outside 09:30-16:00 ET or early in the session; it takes about a
minute. Stock: SPY by default (--symbol), which the paper book does not hold; pick another if it does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as _time
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.broker.protocol import BrokerError, OrderRequest  # noqa: E402

NY = ZoneInfo("America/New_York")
OUT = ROOT / "docs" / "ops" / "alpaca_idempotency_probe.md"
PREFIX = "probe-idem-"
SEND_WINDOW = (time(15, 45), time(16, 0))  # the engine's near-close orders: the probe stays out of it


class Refused(RuntimeError):
    """The probe will not run now (the reason is the message). Nothing was sent."""


class Venue:
    """The few raw calls the probe needs, over `AlpacaPaper` (raw: the adapter's own handling of a duplicate
    client id, which reads the existing order back, would hide the venue's answer)."""

    def __init__(self, broker) -> None:
        self.b = broker

    def post(self, req: OrderRequest) -> tuple[int, dict | str]:
        try:
            return 200, self.b._t("POST", "/v2/orders", body=req.body())
        except BrokerError as e:
            return e.status, str(e.body)[:300]

    def by_client_id(self, coid: str) -> dict | None:
        return self.b.order_by_client_id(coid)

    def by_id(self, order_id: str) -> dict | None:
        try:
            return self.b.order(order_id)
        except BrokerError as e:
            if e.status == 404:
                return None
            raise

    def open_orders(self) -> list[dict]:
        return self.b.open_orders()

    def positions(self) -> dict:
        return self.b.positions()

    def cancel(self, order_id: str) -> None:
        self.b.cancel(order_id)


def check_start(v: Venue, symbol: str, now: datetime) -> None:
    et = now.astimezone(NY).time()
    if SEND_WINDOW[0] <= et < SEND_WINDOW[1]:
        raise Refused("15:45-16:00 ET is the engine's send window: run the probe at another time")
    if symbol in v.positions():
        raise Refused(f"the paper account holds {symbol}: choose a stock it does not hold (--symbol)")
    if any(o.get("symbol") == symbol for o in v.open_orders()):
        raise Refused(f"{symbol} has an open order at the paper account: choose another stock (--symbol)")


def first_seen(read, watch_s: float, every_s: float, clock, sleep) -> tuple[float | None, int]:
    """Seconds until `read()` is true for the first time (None: never within `watch_s`), and the reads made."""
    t0, n = clock(), 0
    while True:
        n += 1
        if read():
            return round(clock() - t0, 3), n
        if clock() - t0 >= watch_s:
            return None, n
        sleep(every_s)


def probe(v: Venue, symbol: str, limit: float, run_id: str, *, watch_s: float = 15.0, every_s: float = 0.5,
          clock=_time.monotonic, sleep=_time.sleep) -> dict:
    """The measurement. Leaves no open order behind (`cleanup` in a finally)."""
    coid = f"{PREFIX}{run_id}"
    req = OrderRequest(symbol, "buy", 1, "limit", "day", coid, limit_price=limit)
    out: dict = {"symbol": symbol, "limit_price": limit, "client_order_id": coid, "steps": {}}
    mine: list[str] = []
    try:
        t0 = clock()
        status, body = v.post(req)
        out["steps"]["submit"] = {"http": status, "seconds": round(clock() - t0, 3),
                                  "status": body.get("status") if isinstance(body, dict) else body}
        if status != 200 or not isinstance(body, dict):
            out["verdict"] = "the first order was refused: nothing measured"
            return out
        oid = str(body["id"])
        mine.append(oid)
        lag = {}
        for name, read in (("by_client_order_id", lambda: v.by_client_id(coid) is not None),
                           ("by_order_id", lambda: v.by_id(oid) is not None),
                           ("open_orders", lambda: any(o.get("id") == oid for o in v.open_orders()))):
            seen, reads = first_seen(read, watch_s, every_s, clock, sleep)
            lag[name] = {"first_seen_after_s": seen, "reads": reads}
        out["steps"]["lookup_lag"] = lag
        dup = {}
        for name, r in (("same_body", req),
                        ("other_quantity", OrderRequest(symbol, "buy", 2, "limit", "day", coid, limit_price=limit))):
            status, body = v.post(r)
            if status == 200 and isinstance(body, dict) and str(body.get("id")) != oid:
                mine.append(str(body["id"]))  # the venue made a second order: recorded, and cancelled below
            dup[name] = {"http": status, "answer": body if isinstance(body, str) else
                         {"id_is_the_first_order": str(body.get("id")) == oid, "status": body.get("status")}}
        dup["open_orders_with_this_client_id"] = sum(o.get("client_order_id") == coid for o in v.open_orders())
        dup["open_orders_in_the_symbol"] = sum(o.get("symbol") == symbol for o in v.open_orders())
        out["steps"]["duplicate_client_order_id"] = dup
        v.cancel(oid)
        seen, reads = first_seen(lambda: (v.by_id(oid) or {}).get("status") == "canceled", watch_s, every_s, clock,
                                 sleep)
        after = v.by_client_id(coid)
        status, body = v.post(req)
        if status == 200 and isinstance(body, dict):
            mine.append(str(body["id"]))
        out["steps"]["after_cancel"] = {
            "canceled_after_s": seen, "reads": reads,
            "by_client_order_id_still_finds_it": after is not None and str(after.get("id")) == oid,
            "same_client_id_again": {"http": status, "answer": body if isinstance(body, str) else
                                     {"new_order": str(body.get("id")) != oid, "status": body.get("status")}}}
        lags = [x["first_seen_after_s"] for x in lag.values()]
        out["verdict"] = {
            "duplicate_refused": all(dup[k]["http"] != 200 for k in ("same_body", "other_quantity"))
            and dup["open_orders_with_this_client_id"] == 1,
            "slowest_lookup_s": None if any(x is None for x in lags) else max(lags),
            "a_lookup_never_saw_the_order": any(x is None for x in lags),
            "client_id_reusable_after_cancel": status == 200,
        }
        return out
    finally:
        out["cleanup"] = cleanup(v, symbol, coid, mine, watch_s, every_s, clock, sleep)


def cleanup(v: Venue, symbol: str, coid: str, ids: list[str], watch_s, every_s, clock, sleep) -> dict:
    left = []
    try:
        todo = set(ids) | {str(o["id"]) for o in v.open_orders() if str(o.get("client_order_id", "")).startswith(coid)}
        for oid in sorted(todo):
            try:
                v.cancel(oid)
            except BrokerError as e:
                left.append(f"{oid}: cancel {e.status}")
        first_seen(lambda: not any(str(o.get("client_order_id", "")).startswith(coid) for o in v.open_orders()),
                   watch_s, every_s, clock, sleep)
        left += [str(o["id"]) for o in v.open_orders() if str(o.get("client_order_id", "")).startswith(coid)]
        return {"orders_left_open": left, "position_in_the_symbol": symbol in v.positions()}
    except Exception as e:  # noqa: BLE001 - the report must say that the clean-up could not be confirmed
        return {"orders_left_open": left or ["unknown"], "error": f"{type(e).__name__}: {e}"[:200],
                "action": f"cancel by hand at Alpaca paper: open orders with client id {coid}*"}


def report(res: dict, when: datetime) -> str:
    return ("# Alpaca paper: repeated client order id and lookup lag (probe result)\n\n"
            f"Run on {when.astimezone(UTC):%Y-%m-%d %H:%M} UTC with `scripts/probe_alpaca_idempotency.py` "
            "(M9 ticket 1.11, issue #436). One DAY limit buy of 1 share at half the bid, cancelled by the probe.\n\n"
            "What the engine relies on (engine/orders.py): a client order id is unique per account, so a second "
            "submission is refused; and an accepted order is found by its client id well inside 60 seconds "
            "(`LOST_AFTER_S`), so an order not found twice by then never reached the venue.\n\n"
            "```json\n" + json.dumps(res, indent=2, sort_keys=True) + "\n```\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", default=os.environ.get("KAIBOT_ENV_FILE"), help="the engine's .env file")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--limit-price", type=float, default=None, help="default: half the stock's last IEX bid")
    ap.add_argument("--watch", type=float, default=15.0, help="seconds each lookup is polled")
    ap.add_argument("--yes", action="store_true", help="place the order (without it: a dry run)")
    ap.add_argument("--write", action="store_true", help=f"save the result as {OUT.relative_to(ROOT)}")
    a = ap.parse_args(argv)
    if not a.yes:
        print(f"dry run: would place ONE paper order (buy 1 {a.symbol}, DAY limit at half the bid), measure the "
              "lookups, repeat its client id three times and cancel it. Add --yes to run.")
        return 0

    from kaibot.broker.alpaca import AlpacaPaper
    from kaibot.engine.alpaca import AlpacaData
    from kaibot.engine.config import PAPER_TRADING_URL, load_env_file

    load_env_file(a.env)
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET") or os.environ.get(
        "ALPACA_API_SECRET")
    if not key or not secret:
        print("ALPACA_API_KEY / ALPACA_SECRET are not set (see --env)", file=sys.stderr)
        return 1
    broker = AlpacaPaper(key, secret)
    if broker.base != PAPER_TRADING_URL or "paper-api" not in broker.base:
        print("paper only: the trading URL is not Alpaca's paper endpoint", file=sys.stderr)
        return 1
    v, now = Venue(broker), datetime.now(UTC)
    try:
        check_start(v, a.symbol, now)
        limit = a.limit_price
        if limit is None:
            q = AlpacaData(key, secret).latest_quotes([a.symbol]).get(a.symbol) or {}
            bid = float(q.get("bp") or 0.0)
            if bid <= 0:
                raise Refused(f"no IEX bid for {a.symbol} now: give --limit-price (about half its last price)")
            limit = round(bid / 2, 2)
        res = probe(v, a.symbol, limit, now.strftime("%Y%m%d%H%M%S"), watch_s=a.watch)
    except Refused as e:
        print(f"not run: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, sort_keys=True))
    if a.write:
        OUT.write_text(report(res, now))
        print(f"written: {OUT.relative_to(ROOT)} (commit it)")
    return 0 if not res.get("cleanup", {}).get("orders_left_open") else 3


if __name__ == "__main__":
    raise SystemExit(main())
