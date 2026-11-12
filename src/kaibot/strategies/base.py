"""The `Strategy` interface (issue #116; decision 15 section 3; audit A1 and section 4, migration step 5).

A strategy is a rule and nothing else. It says when it wants to be asked (`schedule`), how long it holds
(`holding`), how much history it needs (`warmup`), and answers `on_event(ctx)` with what it wants to own:

- an intraday strategy yields `Signal`s (one proposed entry each, with its own stop and target);
- a multi-day strategy returns a `TargetList` (the book it wants after the next fill).

It also owns its exit rules: `on_fill` re-anchors the stop and target on the real fill, `manage` moves the stop
while a position is open, `close_out` asks for an exit of its own (market-on-close, say). A strategy never sends,
replaces or cancels an order and never sizes a position: the engine gates every signal through `risk/gate.py`,
sizes it, and turns the answers above into broker calls. The engine only reads these attributes and calls these
methods; it holds no strategy name of its own (strategies/registry.py lists them).

Shadow strategies (`mode = "shadow"`) are asked the same way but never trade: `engine/shadow.py` hands them the
session panel (`ShadowContext`) and records their candidates as `signal` rows with mode="shadow".

Example: a multi-day target-list strategy, the banded 12-1 momentum rule of `docs/prereg/mom_12_1_band.md`. It
is NOT registered (it has not passed Gate B, and the engine has no order manager for target lists before S78); it
shows the shape the next rule plugs into::

    class MomBand:
        name = "mom_12_1_band"
        mode = "shadow"                      # target lists are recorded, not traded, until S77-S78 land
        holding = Holding.MULTI_DAY
        schedule = (When(Event.AFTER_CLOSE),)
        warmup = 273                         # sessions: the 12-month return needs 252 + 21
        owner_filters = room = False

        def on_event(self, ctx: Context) -> TargetList | None:
            if not ctx.first_session_of_month:   # decide monthly, on data up to today's close
                return None
            closes = ctx.daily_closes(self.warmup)   # point-in-time: rows <= ctx.session only
            score = blend_of_ranks(closes, lookbacks=(126, 189, 252), skip=21)
            held = set(ctx.positions(self.name))
            top10, top20 = top_fraction(score, 0.10), top_fraction(score, 0.20)
            keep = [s for s in held if s in top20]                 # exit only below the top 20%
            new = [s for s in top10 if s not in held][: 20 - len(keep)]
            return TargetList(self.name, ctx.session, {s: 1.0 for s in keep + new},
                              sizing="atr_risk", reasons=("12-1 momentum band",),
                              features={"ranked": len(score)})

        # exits are part of the target list (a name that is no longer in it is sold); no stop to manage
        def on_fill(self, fill, risk_ps): return fill - risk_ps, None
        def manage(self, ctx, pos): return None
        def close_out(self, ctx, pos): return None

The portfolio builder (S79, `risk/portfolio.py`) turns the weights into whole shares (shares = equity x 0.001 /
ATR(20) under the level's caps), and the order manager (S78) sends only the difference to the broker's positions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Holding(StrEnum):
    INTRADAY = "intraday"  # flat by the 15:55 flatten
    MULTI_DAY = "multi_day"  # held across sessions (decision 14); needs the ledger of S77


class Mode(StrEnum):
    LIVE = "live"  # trades on paper when a level (and the owner's config) turns it on
    SHADOW = "shadow"  # "would have" signals only; never reaches the broker


class Event(StrEnum):
    """The clock events of decision 15. The engine delivers BAR_CLOSE once per completed regular-session minute
    and AFTER_CLOSE once per session; the others are declared for the always-on scheduler (S90)."""

    PRE_OPEN = "pre_open"
    SESSION_OPEN = "session_open"
    BAR_CLOSE = "bar_close"
    PRE_CLOSE = "pre_close"
    SESSION_CLOSE = "session_close"
    AFTER_CLOSE = "after_close"


@dataclass(frozen=True)
class When:
    """One entry of a schedule. For BAR_CLOSE: `minute` (a single session minute, 0 = the 09:30 bar), or
    `every` / `offset` (minutes m with m % every == offset: every=5, offset=4 is each completed 5-minute bar).
    `full_session_only`: skip early-close sessions."""

    event: Event
    minute: int | None = None
    every: int | None = None
    offset: int = 0
    full_session_only: bool = False

    def due(self, event: Event, m: int, n_min: int) -> bool:
        if event is not self.event or (self.full_session_only and n_min != 390):
            return False
        if self.minute is not None:
            return m == self.minute
        if self.every is not None:
            return m % self.every == self.offset
        return True


def due(schedule: Iterable[When], event: Event, m: int = -1, n_min: int = 390) -> bool:
    return any(w.due(event, m, n_min) for w in schedule)


@dataclass(frozen=True)
class Signal:
    """One proposed long entry from an intraday strategy, decided at the close of minute `m`.

    `veto` is the rule's own reason not to trade (logged with the signal, nothing is sent). `sigma_1m` and `atr`
    feed the shadow cost model and the trade record; `vix` the size halving. `room`: the candidate row handed to
    the agent room when the strategy uses it (it may only veto or halve)."""

    symbol: str
    entry: float
    stop: float
    target: float | None  # None: no profit target; the strategy's close_out or the 15:55 flatten ends it
    features: dict
    reasons: list[str]
    veto: str | None
    sigma_1m: float
    atr: float
    vix: float | None = None
    room: Any = None


@dataclass(frozen=True)
class TargetList:
    """What a multi-day strategy wants to hold after the next fill: symbol -> relative weight (sizing turns it
    into whole shares). A symbol held by this strategy and missing here is to be sold.

    `index_filter`: the rule declares the index trend filter (pre-registered): while SPY is below its 200-day
    average (or that is unknown) the portfolio builder allows no new buys for it; sells still go through.
    `resize`: the builder may re-size held names this time (False: they keep their shares; a pre-registration that
    resizes only on some decisions says so here). The order of `weights` is the list's priority, best first (the
    builder's ranked fill buys in it)."""

    strategy: str
    session: date
    weights: Mapping[str, float]
    sizing: str = "atr_risk"
    reasons: tuple[str, ...] = ()
    features: dict = field(default_factory=dict)
    index_filter: bool = False
    resize: bool = True


@dataclass(frozen=True)
class MoveStop:
    """Move the protective stop to `price` (only ever up for a long)."""

    price: float


@dataclass(frozen=True)
class CloseOut:
    """Exit the whole position now. `how`: "moc" (market-on-close) or "market"."""

    how: str
    reason: str


ExitAction = MoveStop | CloseOut


@dataclass
class Context:
    """What the engine shows a live strategy at one event. Reads only; nothing here can place an order.

    `holds(symbol)`: the engine already has a position (any strategy) in it. `prefetch(symbols)`: read quotes and
    buying power once for a batch of candidates (otherwise the engine reads them per signal). `scan`: the
    dashboard's scan counters (never read by decisions). `owner_veto` / `extra_veto` are the owner's tighten-only
    filters and the rules-only extra veto (pump risk); the engine also applies them to strategies with
    `owner_filters`, the strategy only calls them to keep its own order of vetoes."""

    event: Event
    m: int
    session: date
    n_min: int
    market: Any  # engine.market.LiveMarket (point-in-time reads)
    rules: Any  # engine.userconfig.Rules for the session, or None before the open
    now: Callable[[], datetime]
    holds: Callable[[str], bool]
    prefetch: Callable[[list[str]], None]
    scan: dict
    owner_veto: Callable[[str, float, float | None], str | None]
    extra_veto: Callable[[str, int], str | None]
    prefetched: bool = False  # set by prefetch()


@dataclass
class ShadowContext:
    """What engine/shadow.py shows a shadow strategy at the close of minute m: the session panel (columns 0..m are
    known, later ones NaN live), the strategy's statistics known before the session and a point-in-time news
    lookup `news_flags(rows, m)` -> bool per row."""

    m: int
    panel: Any  # strategies.shadow_types.Panel
    stats: dict
    news_flags: Callable[[Any, int], Any]
    seen: set  # (strategy, symbol) already signalled today


@runtime_checkable
class Strategy(Protocol):
    name: str
    mode: Mode
    holding: Holding
    schedule: tuple[When, ...]
    warmup: int  # sessions of history needed before the first decision
    owner_filters: bool  # the owner's price/spread/VIX filters, the extra veto and the scan funnel apply
    room: bool  # its signals may go to the agent room (veto or halve only)

    def on_event(self, ctx: Any) -> Iterable[Signal] | TargetList | None: ...

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]: ...

    def manage(self, ctx: Context, pos: Any) -> ExitAction | None: ...

    def close_out(self, ctx: Context, pos: Any) -> ExitAction | None: ...


# ---------------- the dashboard's scan funnel (counters only; never read by a decision) ----------------
def new_scan() -> dict:
    return {"bars": 0, "checks": 0, "regime_off": 0, "setups": 0, "near": 0, "vetoed": {}, "to_room": 0,
            "recent_near": []}


def note_scan(sc: dict, m: int, checked: int, near: list, setups: int) -> None:
    """Dashboard counters for one completed 5-minute bar: symbols checked, setups, near-misses (last 5 kept)."""
    if (m + 1) % 5 != 0:
        return
    sc["bars"] += 1
    sc["checks"] += int(checked)
    sc["setups"] += int(setups)
    sc["near"] += len(near)
    sc["recent_near"] = (sc["recent_near"] + list(near))[-5:]
