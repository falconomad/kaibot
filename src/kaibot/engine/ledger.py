"""The ledger (S77, #117; decision 15 section 3): positions per (strategy, symbol), cash, a mark-to-market close per
session, dividends and splits. Its state is a pure fold over journal entries (engine/journal.py), in journal order;
nothing here reads a clock, the broker or a database.

Entries (kind, body), every body carries `run_id`:

    run       {amount, prior_days?, prior_cash?, broker_cash0?}  a run starts (or, for a run that began before the
              ledger existed, is imported: its realised daily results so far and the cash they leave)
    position  {strategy, symbol, qty, px, session}               an open position imported with its run
    fill      {strategy, symbol, side, qty, px, fees, pnl_venue?, pnl_shadow?}
    dividend  {symbol, ex_date, per_share | amount, ref}        cash to every sleeve holding the symbol
    split     {symbol, ex_date, ratio, price?, ref}             new shares per old share; a fraction is paid in cash
    mark      {session, prices {(strategy, symbol) | "strategy|symbol": px}, pnl_venue, pnl_shadow, ts?}
              the session's close: every open sleeve is marked, the day is closed

Two equity figures per close:

- `close_equity` (what the engine sizes and stops on, "report the worse"): the pot, plus the reported result of
  every closed session (the lower of venue and shadow, exactly the figure `daily_result.pnl_reported` holds), plus
  the open sleeves at their marks against their cost, plus dividends. With no open position this is, to the last
  bit, the start equity the engine used before the ledger (pot + sum of reported daily results), so intraday
  sizing does not move.
- `close_equity_venue`: cash plus the open sleeves at their marks (the broker's view).

Start equity of a session is the last close (`start_equity`), i.e. yesterday's mark-to-market equity.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field

LEDGER_KINDS = ("run", "position", "fill", "dividend", "split", "mark")


@dataclass
class Sleeve:
    strategy: str
    symbol: str
    qty: float = 0.0
    cost: float = 0.0  # total cost basis of the shares held, buy fees included
    opened: str | None = None  # session of the first buy (or of the import)
    mark: float | None = None  # last close mark; None until the sleeve has been held over a close
    dividends: float = 0.0

    @property
    def avg_cost(self) -> float:
        return self.cost / self.qty if self.qty else 0.0


@dataclass
class Close:
    session: str
    start_equity: float
    close_equity: float
    close_equity_venue: float
    cash: float
    invested: float
    realized_venue: float  # the day's, from fills
    pnl_venue: float  # the day's result as the engine reports it (daily_result)
    pnl_shadow: float
    dividends: float  # the day's
    positions: list[dict] = field(default_factory=list)
    ts: str | None = None

    @property
    def change(self) -> float:
        return self.close_equity - self.start_equity


@dataclass
class Book:
    run_id: int
    amount: float
    cash: float
    prior_days: list[float] = field(default_factory=list)  # reported results of sessions closed before the import
    days: list[float] = field(default_factory=list)  # reported results of sessions closed by this ledger
    sleeves: dict[tuple[str, str], Sleeve] = field(default_factory=dict)
    closes: list[Close] = field(default_factory=list)
    dividends_total: float = 0.0
    realized_venue: float = 0.0  # since the run began (fills only)
    day_realized: float = 0.0
    day_dividends: float = 0.0
    booked: set = field(default_factory=set)  # refs of corporate actions already booked
    broker_cash0: float | None = None
    # #206: realised P&L and dividends since the run began, per strategy (the multi-day book's own P&L)
    realized_by: dict[str, float] = field(default_factory=dict)
    dividends_by: dict[str, float] = field(default_factory=dict)

    def strategy_pnl(self, strategies, prices: Mapping[tuple[str, str], float] | None = None) -> float:
        """P&L since the run began of these strategies' sleeves: realised, dividends, and the open sleeves at
        `prices` (missing: the last mark, else the cost). The multi-day book's equity is the pot plus this."""
        names = set(strategies)
        out = sum(v for k, v in self.realized_by.items() if k in names)
        out += sum(v for k, v in self.dividends_by.items() if k in names)
        for k, sl in self.sleeves.items():
            if k[0] in names:
                px = (prices or {}).get(k)
                px = px if px is not None else (sl.mark if sl.mark is not None else sl.avg_cost)
                out += sl.qty * px - sl.cost
        return out

    def qty_by_symbol(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for (_, sym), s in self.sleeves.items():
            out[sym] = out.get(sym, 0.0) + s.qty
        return out

    def holders(self, symbol: str) -> list[Sleeve]:
        return [s for (_, sym), s in self.sleeves.items() if sym == symbol and s.qty > 0]

    def equity(self, prices: Mapping[tuple[str, str], float]) -> float:
        """Reported-basis equity at these prices (missing price: the last mark, else the cost)."""
        reported = self.amount + float(sum(self.prior_days + self.days))
        open_term = 0.0
        for k, s in self.sleeves.items():
            px = prices.get(k)
            px = px if px is not None else (s.mark if s.mark is not None else s.avg_cost)
            open_term += s.qty * px - s.cost
        return reported + open_term + self.dividends_total


class Ledger:
    def __init__(self) -> None:
        self.books: dict[int, Book] = {}

    @classmethod
    def fold(cls, entries: Iterable) -> Ledger:
        """Entries are (kind, body) pairs or journal rows (seq, ts, kind, body)."""
        lg = cls()
        for e in entries:
            kind, body = (e[2], e[3]) if len(e) == 4 else (e[0], e[1])
            lg.apply(kind, body)
        return lg

    def book(self, run_id) -> Book | None:
        return self.books.get(int(run_id)) if run_id is not None else None

    def start_equity(self, run_id) -> float | None:
        b = self.book(run_id)
        if b is None:
            return None
        return b.closes[-1].close_equity if b.closes else b.equity({})

    # ---------------------------------------------------------------- the fold
    def apply(self, kind: str, body: dict) -> None:
        if kind not in LEDGER_KINDS:
            return
        rid = int(body["run_id"])
        if kind == "run":
            prior = [float(x) for x in body.get("prior_days") or []]
            amount = float(body["amount"])
            self.books[rid] = Book(rid, amount, float(body.get("prior_cash", amount)), prior_days=prior,
                                   broker_cash0=body.get("broker_cash0"))
            return
        b = self.books.get(rid)
        if b is None:  # an entry for a run this ledger never opened: nothing to fold it into
            return
        getattr(self, f"_{kind}")(b, body)

    def _position(self, b: Book, e: dict) -> None:
        s = self._sleeve(b, e["strategy"], e["symbol"], e.get("session"))
        qty, px = float(e["qty"]), float(e["px"])
        s.qty += qty
        s.cost += qty * px
        b.cash -= qty * px
        if e.get("mark") is not None:
            s.mark = float(e["mark"])

    def _fill(self, b: Book, e: dict) -> None:
        qty, px, fees = float(e["qty"]), float(e["px"]), float(e.get("fees") or 0.0)
        key = (e["strategy"], e["symbol"])
        if e["side"] == "buy":
            s = self._sleeve(b, *key, e.get("session"))
            s.qty += qty
            s.cost += qty * px + fees
            b.cash -= qty * px + fees
            return
        s = b.sleeves.get(key)
        basis = s.avg_cost * qty if s is not None else qty * px
        b.cash += qty * px - fees
        b.realized_venue += qty * px - fees - basis
        b.day_realized += qty * px - fees - basis
        b.realized_by[e["strategy"]] = b.realized_by.get(e["strategy"], 0.0) + qty * px - fees - basis
        if s is None:
            return
        s.cost -= basis
        s.qty -= qty
        if s.qty <= 1e-9:
            del b.sleeves[key]

    def _dividend(self, b: Book, e: dict) -> None:
        if self._seen(b, e):
            return
        holders = b.holders(e["symbol"])
        total_qty = sum(s.qty for s in holders)
        if not holders or total_qty <= 0:
            return
        for s in holders:
            cash = s.qty * float(e["per_share"]) if e.get("per_share") is not None \
                else float(e["amount"]) * s.qty / total_qty
            s.dividends += cash
            b.dividends_by[s.strategy] = b.dividends_by.get(s.strategy, 0.0) + cash
            b.cash += cash
            b.dividends_total += cash
            b.day_dividends += cash

    def _split(self, b: Book, e: dict) -> None:
        if self._seen(b, e):
            return
        ratio = float(e["ratio"])
        if not (ratio > 0 and math.isfinite(ratio)):
            return
        for s in b.holders(e["symbol"]):
            exact = s.qty * ratio
            whole = math.floor(exact + 1e-9)
            frac = exact - whole
            if frac > 1e-9 and e.get("price") is not None:  # cash in lieu of the fraction, at the price given
                b.cash += frac * float(e["price"])
                cost_out = s.cost * frac / exact
                s.cost -= cost_out
                b.realized_venue += frac * float(e["price"]) - cost_out
                b.realized_by[s.strategy] = b.realized_by.get(s.strategy, 0.0) + frac * float(e["price"]) - cost_out
                b.day_realized += frac * float(e["price"]) - cost_out
                s.qty = float(whole)
            else:
                s.qty = exact
            if s.mark is not None:
                s.mark /= ratio

    def _mark(self, b: Book, e: dict) -> None:
        prices = {_key(k): float(v) for k, v in (e.get("prices") or {}).items() if v is not None}
        start = b.closes[-1].close_equity if b.closes else b.equity({})
        for k, s in b.sleeves.items():
            if k in prices:
                s.mark = prices[k]
            elif s.mark is None:
                s.mark = s.avg_cost  # no price at all: held at cost, and said so in the position row
        venue, shadow = float(e.get("pnl_venue") or 0.0), float(e.get("pnl_shadow") or 0.0)
        b.days.append(min(venue, shadow))
        invested = sum(s.qty * s.mark for s in b.sleeves.values())
        positions = [{"strategy": s.strategy, "symbol": s.symbol, "qty": s.qty, "avg_cost": s.avg_cost,
                      "mark": s.mark, "priced": (s.strategy, s.symbol) in prices, "opened": s.opened,
                      "dividends": s.dividends} for s in b.sleeves.values()]
        b.closes.append(Close(str(e["session"]), start, b.equity({}), b.cash + invested, b.cash, invested,
                              b.day_realized, venue, shadow, b.day_dividends, positions, e.get("ts")))
        b.day_realized = b.day_dividends = 0.0

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _sleeve(b: Book, strategy: str, symbol: str, session: str | None) -> Sleeve:
        key = (strategy, symbol)
        if key not in b.sleeves:
            b.sleeves[key] = Sleeve(strategy, symbol, opened=session)
        return b.sleeves[key]

    @staticmethod
    def _seen(b: Book, e: dict) -> bool:
        ref = e.get("ref") or f"{e['symbol']}:{e['ex_date']}:{e.get('ratio')}:{e.get('per_share')}:{e.get('amount')}"
        if ref in b.booked:
            return True
        b.booked.add(ref)
        return False

    def snapshot(self, run_id) -> dict:
        b = self.book(run_id)
        return {"cash": b.cash, "sleeves": {f"{k[0]}|{k[1]}": asdict(s) for k, s in b.sleeves.items()},
                "closes": [{**asdict(c), "change": c.change} for c in b.closes]}


def _key(k) -> tuple[str, str]:
    if isinstance(k, tuple | list):
        return str(k[0]), str(k[1])
    strategy, _, symbol = str(k).partition("|")
    return strategy, symbol


def price_key(strategy: str, symbol: str) -> str:
    """How a mark entry names a sleeve in JSON."""
    return f"{strategy}|{symbol}"


# ---------------------------------------------------------------- corporate actions
@dataclass(frozen=True)
class Action:
    symbol: str
    ex_date: str
    kind: str  # "split" | "dividend"
    ratio: float | None = None  # split: new shares per old share
    per_share: float | None = None  # dividend per share (research: the daily store's table)
    amount: float | None = None  # dividend in dollars for the whole account (live: Alpaca's net amount)
    price: float | None = None  # split: price for cash in lieu of a fraction
    ref: str = ""

    def entry(self, run_id) -> tuple[str, dict]:
        body = {"run_id": run_id, "symbol": self.symbol, "ex_date": self.ex_date, "ref": self.ref}
        for k in ("ratio", "per_share", "amount", "price"):
            if getattr(self, k) is not None:
                body[k] = getattr(self, k)
        return self.kind, body


SPLIT_TYPES = ("forward_split", "reverse_split", "stock_dividend")


def actions_from_daily_store(df, session: str) -> list[Action]:
    """Research: the daily store's corporate-actions table (docs/ops/daily_bars.md) for one ex-date. Splits and
    stock dividends change the share count by `ratio`; cash dividends pay `cash` per share. Duplicates (`dup`) and
    information-only types (spin-offs, name changes) are skipped."""
    out = []
    if df is None or not len(df):
        return out
    for r in df.to_dict("records"):
        if bool(r.get("dup")) or str(r.get("ex_date"))[:10] != str(session):
            continue
        ref = f"daily:{r.get('id')}"
        if r.get("type") in SPLIT_TYPES and r.get("ratio"):
            out.append(Action(str(r["symbol"]), str(session), "split", ratio=float(r["ratio"]), ref=ref))
        elif r.get("type") == "cash_dividend" and r.get("cash"):
            out.append(Action(str(r["symbol"]), str(session), "dividend", per_share=float(r["cash"]), ref=ref))
    return out


DIVIDEND_ACTIVITIES = ("DIV", "DIVNRA", "DIVFEE", "DIVROC", "DIVTXEX", "DIVCGL", "DIVCGS", "DIVFT", "DIVTW", "CGD")
SPLIT_ACTIVITIES = ("SSP",)
CORPORATE_ACTIVITIES = DIVIDEND_ACTIVITIES + SPLIT_ACTIVITIES


def actions_from_activities(rows: Iterable[dict], ledger_qty: Mapping[str, float],
                            broker_qty: Mapping[str, float]) -> list[Action]:
    """Live: Alpaca's non-trade account activities. A dividend books its `net_amount` (withholding and fees are
    their own negative rows). Alpaca does not document the fields of a split row (SSP), so the ratio is never read
    from it: it is the broker's share count after the split over the ledger's before. With either count unknown the
    split is not booked here; the drift check then stops entries and says why."""
    out = []
    for r in rows:
        t, sym = r.get("activity_type"), r.get("symbol")
        if not sym:
            continue
        ref, day = f"alpaca:{r.get('id')}", str(r.get("date") or "")[:10]
        if t in DIVIDEND_ACTIVITIES and r.get("net_amount") is not None:
            out.append(Action(sym, day, "dividend", amount=float(r["net_amount"]), ref=ref))
        elif t in SPLIT_ACTIVITIES:
            before, after = float(ledger_qty.get(sym) or 0), float(broker_qty.get(sym) or 0)
            if before > 0 and after > 0 and after != before:
                out.append(Action(sym, day, "split", ratio=after / before, ref=ref))
    return out
