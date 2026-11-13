"""Overnight-safe fail-safes (S80, issue #120; decision 15 sections 4 and 6; audit B1 items 7 and 8). Pure.

When the engine is silent, the watchdog (engine/watchdog.py) and the database dead-man (migration 0019) no longer
sell the whole account. Both follow one plan, built here from what the broker says (positions, open orders) and
what the book says (which sleeve holds which shares, at which stop):

1. cancel every open ENTRY order of Kaibot not yet completely filled (a partly filled one has its rest cancelled):
   a buy whose client order id starts with `kb`, the intraday `kb-<run>-<signal>-<n>` and the order manager's
   `kb<run>-<strategy>-<symbol>-...`;
2. verify that every multi-day sleeve has its protective stop resting at the broker, and re-create a missing one
   (a GTC stop sell, whole shares, at the sleeve's last known stop; if none is known or it is not below the last
   price, `EMERGENCY_STOP_PCT` below the last price), never for more shares than the position has free;
3. raise the alarm (the caller writes the halt and the event);
4. at the close minus 5 minutes (watchdog) or 4 minutes (dead-man), sell only the INTRADAY shares: the shares of
   a symbol that no multi-day sleeve explains. With no multi-day shares anywhere this is exactly the old flatten.

Which shares are multi-day, per symbol: the larger of what the book says (the local journal, else the ledger rows in
Supabase) and what the multi-day stops at the broker cover (their client ids name the strategy and the symbol), and
never more than the broker holds. Fail closed: a position that neither the book nor a multi-day stop explains is
treated as intraday, so an unprotected position nobody can attribute is not carried over the close.

The SQL in migrations/0019_deadman_overnight.sql implements the same plan; tests/test_failsafe_drills.py runs both on
the same accounts and compares them.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from kaibot.risk.gate import GAP_SCENARIO_PCT

EMERGENCY_STOP_PCT = GAP_SCENARIO_PCT  # 10%: the gap the book is sized to survive (risk/gate.py)
OPEN_STATES = frozenset({"new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding",
                         "pending_replace"})
LEG_OPEN = frozenset({"new", "held", "accepted", "pending_new"})
# the order manager's client ids (engine/orders.client_order_id): kb<run>-<strategy>-<symbol>-<yyyymmdd>-<purpose>-<n>
COID = re.compile(r"^kb(\d+)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-(\d{8})-([A-Za-z0-9_.]+)-(\d+)$")
# the stops the fail-safes re-create: kbwd-... (watchdog), kbdm-... (database dead-man, migration 0019)
FAILSAFE_COID = re.compile(r"^kb(wd|dm)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-(\d{8,14})-([A-Za-z0-9_.]+)$")
EPS = 1e-9


def clean(x) -> str:
    """As engine/orders.client_order_id writes a name into a client id."""
    return re.sub(r"[^A-Za-z0-9_.]", "", str(x))


def multi_day_names() -> tuple[str, ...]:
    from kaibot.strategies import registry
    from kaibot.strategies.base import Holding

    return tuple(sorted({s.name for s in registry.MULTI_DAY}
                        | {s.name for s in registry.STRATEGIES if s.holding is Holding.MULTI_DAY}))


def is_multi_day(strategy: str | None, names: Iterable[str] | None = None) -> bool:
    """A strategy (or the strategy part of a client id) that holds across sessions. Neighbours of a rule
    (`trend_momentum~reg60`) count as the rule."""
    if not strategy:
        return False
    s = clean(strategy)
    return any(s.startswith(clean(n)) for n in (names if names is not None else multi_day_names()))


def parse_coid(coid: str | None) -> tuple[str, str, str] | None:
    """(strategy, symbol, purpose) of an order manager client id; None for anything else."""
    m = COID.match(str(coid or "")) or FAILSAFE_COID.match(str(coid or ""))
    return (m.group(2), m.group(3), m.group(5)) if m else None


@dataclass(frozen=True)
class SleeveView:
    strategy: str
    symbol: str
    qty: float
    stop: float | None = None  # the sleeve's protective stop price, if known


@dataclass
class BookView:
    """Who holds what, as the fail-safes see it. `source`: journal | store | none."""

    sleeves: list[SleeveView] = field(default_factory=list)
    source: str = "none"
    run_id: int | None = None

    def multi_day(self, names: Iterable[str] | None = None) -> list[SleeveView]:
        return [s for s in self.sleeves if s.qty > EPS and is_multi_day(s.strategy, names)]


@dataclass(frozen=True)
class StopOrder:
    strategy: str
    symbol: str
    qty: int
    stop: float
    why: str  # "known stop" | "emergency: ..."


@dataclass
class Plan:
    cancel: list[dict] = field(default_factory=list)  # entry orders to cancel: {id, symbol}
    stops: list[StopOrder] = field(default_factory=list)
    close: list[str] = field(default_factory=list)  # symbols with intraday shares only: close the position
    sell: list[tuple[str, int, list[str]]] = field(default_factory=list)  # (symbol, intraday shares, orders to cancel)
    multi_day: dict[str, float] = field(default_factory=dict)  # symbol -> shares kept over the close
    unprotected: dict[str, float] = field(default_factory=dict)  # symbol -> multi-day shares no stop could cover
    alarms: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"cancel": [o["symbol"] for o in self.cancel],
                "stops": [{"strategy": s.strategy, "symbol": s.symbol, "qty": s.qty, "stop": s.stop, "why": s.why}
                          for s in self.stops],
                "close": list(self.close), "sell": [{"symbol": s, "qty": q} for s, q, _ in self.sell],
                "multi_day": {k: round(v, 6) for k, v in self.multi_day.items()},
                "unprotected": {k: round(v, 6) for k, v in self.unprotected.items()}, "alarms": list(self.alarms)}


def is_entry(o: dict) -> bool:
    """An open Kaibot buy not yet completely filled: an entry (intraday) or a target purchase (multi-day). A partly
    filled one counts (as before S80): cancelling its rest only takes risk off, and the shares already filled keep
    their bracket legs (intraday) or get their protective stop (multi-day). `filled_at` is set only by the fill
    that completes an order (Alpaca, SimBroker)."""
    return (o.get("side") == "buy" and str(o.get("client_order_id") or "").startswith("kb")
            and o.get("status") in OPEN_STATES and not o.get("filled_at"))


def _left(o: dict) -> float:
    return max(float(o.get("qty") or 0) - float(o.get("filled_qty") or 0), 0.0)


def _open_sells(orders: Iterable[dict], symbol: str) -> list[dict]:
    """Working sell orders and legs on `symbol` (each holds shares at the venue)."""
    out = []
    for o in orders:
        if o.get("symbol") != symbol:
            continue
        if o.get("side") == "sell" and o.get("status") in OPEN_STATES:
            out.append(o)
        for leg in o.get("legs") or []:
            if leg.get("side", "sell") == "sell" and leg.get("status") in LEG_OPEN and o.get("status") == "filled":
                out.append({**leg, "symbol": symbol, "qty": leg.get("qty") or o.get("filled_qty") or o.get("qty")})
    return out


def _md_stop(o: dict, names) -> tuple[str, str] | None:
    """(strategy, symbol) of a resting multi-day protective stop, from its client id."""
    if o.get("type") not in ("stop", "stop_limit") or o.get("side") != "sell":
        return None
    p = parse_coid(o.get("client_order_id"))
    return (p[0], p[1]) if p is not None and is_multi_day(p[0], names) else None


def plan(positions: Mapping[str, dict], orders: list[dict], book: BookView | None, *, flatten: bool,
         names: Iterable[str] | None = None) -> Plan:
    """The fail-safe plan for a silent engine. `positions`: the broker's, by symbol (qty, current_price); `orders`:
    its open orders (Alpaca shape, legs nested); `flatten`: it is the close minus 5 (or 4) minutes."""
    names = tuple(names if names is not None else multi_day_names())
    out = Plan()
    out.cancel = [{"id": o["id"], "symbol": o.get("symbol")} for o in orders if is_entry(o)]
    canceled = {o["id"] for o in out.cancel}
    live = [o for o in orders if o.get("id") not in canceled]
    sleeves = book.multi_day(names) if book is not None else []
    for sym in sorted(positions):
        p = positions[sym]
        have = float(p.get("qty") or 0)
        if have <= EPS:
            continue
        sells = _open_sells(live, sym)
        md_orders = {}
        for o in sells:
            k = _md_stop(o, names)
            if k is not None:
                md_orders.setdefault(clean(k[0]), []).append(o)
        covered = {s: sum(_left(o) for o in os) for s, os in md_orders.items()}
        mine = [s for s in sleeves if s.symbol == sym]
        book_md = sum(s.qty for s in mine)
        md = min(max(book_md, sum(covered.values())), have)
        free = max(have - sum(_left(o) for o in sells), 0.0)
        last = float(p.get("current_price") or 0) or None
        for s in mine:
            want = int(math.floor(s.qty + EPS)) - int(math.floor(covered.get(clean(s.strategy), 0.0) + EPS))
            if want < 1:
                continue
            q = min(want, int(math.floor(free + EPS)))
            px, why = _stop_price(s.stop, last)
            if q >= 1 and px is not None:
                out.stops.append(StopOrder(s.strategy, sym, q, px, why))
                free -= q
            if q < want or px is None:
                miss = want - (q if px is not None else 0)
                out.unprotected[sym] = out.unprotected.get(sym, 0.0) + miss
                out.alarms.append(f"{sym} ({s.strategy}): {miss} multi-day shares without a protective stop "
                                  f"({'no price known' if px is None else 'shares held by other orders'})")
        if md > EPS:
            out.multi_day[sym] = md
        intraday = int(math.floor(have - md + EPS))
        if not flatten or intraday < 1:
            continue
        if md <= EPS:
            out.close.append(sym)
        else:  # keep the multi-day shares and their stops; free and sell the rest
            others = [o["id"] for o in sells if _md_stop(o, names) is None and o.get("id")]
            out.sell.append((sym, intraday, others))
    if book is None or book.source == "none":
        out.alarms.append("book unknown: multi-day shares taken from the resting multi-day stops only")
    return out


def _stop_price(stop: float | None, last: float | None) -> tuple[float | None, str]:
    if stop is not None and stop > 0 and (last is None or stop < last):
        return round(float(stop), 2), "known stop"
    if last is None:
        if stop is not None and stop > 0:
            return round(float(stop), 2), "known stop (no last price)"
        return None, "no stop and no last price"
    px = math.floor(last * (1 - EMERGENCY_STOP_PCT / 100) * 100) / 100
    return px, (f"emergency: {EMERGENCY_STOP_PCT:g}% below the last price "
                + ("(no stop known)" if stop is None else f"(stop {stop:g} not below the last price {last:g})"))


# ---------------------------------------------------------------- the book, read without the engine


class ReadOnlyJournal:
    """The engine's local journal (engine/journal.py), opened read-only: the watchdog never writes to it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def entries(self, after: int = 0, kinds: tuple[str, ...] | None = None, limit: int | None = None) -> list:
        import json

        if not self.path.exists():
            raise FileNotFoundError(self.path)
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2.0)
        try:
            q, args = "select seq, ts, kind, body from entry where seq > ?", [after]
            if kinds:
                q += f" and kind in ({','.join('?' * len(kinds))})"
                args += list(kinds)
            q += " order by seq"
            rows = db.execute(q, args).fetchall()
        finally:
            db.close()
        return [(s, ts, k, json.loads(b)) for s, ts, k, b in rows]


def stops_from_journal(journal) -> dict[tuple[str, str], float]:
    """The last protective stop price the order manager sent per (strategy, symbol), whatever became of it."""
    from kaibot.engine.orders import OM_KINDS, OrderManager

    om = OrderManager(None, _Entries(journal.entries(kinds=OM_KINDS)), lambda: None)  # a fold only: nothing is sent
    out: dict[tuple[str, str], float] = {}
    for r in sorted(om.orders.values(), key=lambda r: r.created or ""):
        if (r.purpose == "stop" or (r.type == "stop" and r.side == "sell")) and r.stop:
            out[(r.strategy, r.symbol)] = float(r.stop)
    return out


class _Entries:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def entries(self, kinds=None, **_):
        return [r for r in self.rows if kinds is None or r[2] in kinds]


def book_from_journal(journal, run_id: int | None = None) -> BookView:
    """The ledger's sleeves of `run_id` (default: the last run the journal opened) with their stops."""
    from kaibot.engine.ledger import LEDGER_KINDS, Ledger

    rows = journal.entries(kinds=LEDGER_KINDS)
    lg = Ledger.fold(rows)
    if run_id is None:
        runs = [int(r[3]["run_id"]) for r in rows if r[2] == "run"]
        run_id = runs[-1] if runs else None
    b = lg.book(run_id)
    if b is None:
        return BookView([], "journal", run_id)
    stops = stops_from_journal(journal)
    return BookView([SleeveView(s.strategy, s.symbol, float(s.qty), stops.get(k)) for k, s in b.sleeves.items()
                     if s.qty > EPS], "journal", run_id)


def book_from_store(store) -> BookView:
    """The ledger rows Supabase has (migration 0018): the active run's positions at its last session close."""
    runs = [r for r in store.select("run", {"status": "in.(running,paused)", "order": "id.desc", "limit": "1"})]
    if not runs:
        return BookView([], "store", None)
    rid = runs[0]["id"]
    last = store.select("ledger_position", {"run_id": f"eq.{rid}", "order": "session.desc", "limit": "1"})
    if not last:
        return BookView([], "store", rid)
    rows = store.select("ledger_position", {"run_id": f"eq.{rid}", "session": f"eq.{last[0]['session']}"})
    return BookView([SleeveView(r["strategy"], r["symbol"], float(r["qty"]),
                                float(r["stop_px"]) if r.get("stop_px") is not None else None) for r in rows],
                    "store", rid)
