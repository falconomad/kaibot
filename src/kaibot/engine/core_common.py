"""What `core.Engine` and the modules it is split into share (#694, audit 2 ticket 5.14): the engine's logger, its
limits and timings, the position and session types (`Pos`, `Positions`, `Day`), its two refusals and the small
time and number helpers.

Moved from engine/core.py unchanged. engine/core.py imports every name back, so `from kaibot.engine.core import
Day` and the rest keep working.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

import numpy as np

from kaibot.broker.alpaca import CANCEL_WAIT_S
from kaibot.engine.reconcile import ACCEPT_COMMAND
from kaibot.engine.userconfig import Rules
from kaibot.strategies import registry
from kaibot.strategies.base import Holding, new_scan

log = logging.getLogger("kaibot.engine")

FIRST_ENTRY_M = 5  # 09:35
PAUSE_AFTER_LOSSES, PAUSE_MINUTES = 3, 60
MAX_ERROR_STREAK = 3  # consecutive failed loop iterations before the kill switch (`engine_errors`) trips
# M9 1.7: clean ticks in a row (one a minute) after which the `engine_errors` kill is cleared; the halt row stays
CLEAN_TICKS_TO_CLEAR = 5
STOPS_TO_PAUSE, STOPS_WINDOW_DAYS = 3, 14  # 3 daily stops within ~10 sessions pause the run (PRD v2)
LEVEL_NAMES = {"conservative": "Steady", "balanced": "Balanced", "aggressive": "Hunter"}
# M9 1.15 (audit S1): a command row is checked in code before anything is done. The pot of a paper run has the
# database's own limits (migrations 0001/0002: `amount_usd between 1000 and 100000`), which a local-first run row
# would otherwise meet only when the replicator sends it; a kind outside COMMAND_KINDS is rejected, never applied.
RUN_AMOUNT_MIN_USD, RUN_AMOUNT_MAX_USD = 1_000.0, 100_000.0
# M10 1.25 (#536): a session whose owner config could not be READ at its start (Supabase unreachable) asks again,
# at most this often, while the lease is ours; a config that was read and rejected is never asked again that day
CONFIG_UNREADABLE = "config unreadable"
RULES_RETRY_S = 60.0
COMMAND_KINDS = ("new_run", "pause", "resume", "flatten_and_stop", ACCEPT_COMMAND, "set_level")
# S80 (#120, decision 15 sections 4 and 6): the close-minus-5 flatten and the daily stop sell intraday sleeves only;
# multi-day sleeves keep their protective stops. Run drawdown, a new run and "Flatten and stop" sell everything.
INTRADAY_ONLY = ("time", "daily_stop")
FLATTEN_TRIES = 2  # a close the venue refused is tried once more inside the same flatten; the loop does the rest
# S74 review (#142): one time limit for a whole flatten, retries included, well inside the watchdog's 90 s heartbeat
# staleness window (watchdog.STALE_S). A close is only started while CLOSE_MAX_S is left: the adapter's own
# bounded cancel wait (CANCEL_WAIT_S) plus its reads. What is left when the budget runs out is reported and retried
# by the next loop pass, never dropped. The heartbeat is written between positions (Engine.on_progress).
FLATTEN_BUDGET_S = 30.0
CALENDAR_ALERT_DAYS = 30  # M9 1.17 (#442): a hand-kept calendar ending within this many days is announced
CLOSE_MAX_S = CANCEL_WAIT_S + 3.0
CANCEL_UNCONFIRMED = 409  # the adapter's "cancel not confirmed in time": already waited, so not retried at once
# S77: the ledger's cash change may differ from the broker's by fees the engine does not model; above this it says so
CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT = 1.0, 0.05
RECHECK_MINUTES = 15  # S78: a blocking reconciliation difference is checked again this often
CLOCK_SKEW_MAX_S = 5.0  # M9 1.10 (#435): the host clock may differ from the broker's by this much, not more
BREACH_CONFIRM_S = 60.0  # #211: a book breach must hold on two checks at least this far apart
# M9 1.1 (audit R1): the prices the book's run end is measured on. A minute bar counts by itself only when it ended
# at most MD_BAR_MAX_AGE_MIN minutes ago and its close is within MD_FAR_PCT % of the holding's last close mark;
# otherwise only when the broker's own price for the position, read at this tick (at most MD_BROKER_MAX_AGE_S ago),
# is within MD_AGREE_PCT % of it. A price that is neither is not used: the holding is measured at its last close mark.
MD_BAR_MAX_AGE_MIN, MD_FAR_PCT, MD_AGREE_PCT, MD_BROKER_MAX_AGE_S = 5.0, 10.0, 2.0, 120.0
# QA-Ops second key on #527: the other direction, a real fall must still end the book. A bar older than
# MD_BAR_DEAD_MIN minutes is no price any more (the market data is down, or the symbol is silent on IEX): the
# broker's price of this tick is then used alone, as for a holding without bars. A holding measured at its last
# close mark for MD_UNCONFIRMED_ALARM_MIN minutes on end raises an event (`multi_day_book_unconfirmed`).
MD_BAR_DEAD_MIN, MD_UNCONFIRMED_ALARM_MIN = 15.0, 5.0


class DailyPathMissing(RuntimeError):
    """M10 1.20 step 2 (#445, Product ruling 24 Nov): the run's ledger holds multi-day sleeves and this engine has
    no daily path (KAIBOT_MULTIDAY off, or no multi-day rule enabled). It refuses to start: the sleeves' sales,
    syncs and bookings belong to the daily path's order manager, and the older flatten sale without it is not
    used again (deleted in step 4). Raised by `Engine.startup` before any order call or start event."""

    def __init__(self, run_id, symbols: list[str], flag_on: bool):
        self.run_id, self.symbols = run_id, list(symbols)
        fix = ("set KAIBOT_MULTIDAY=on in the engine's .env and restart" if not flag_on else
               "KAIBOT_MULTIDAY is on but no multi-day rule is enabled in config/profiles.yaml (`multi_day.rules`): "
               "enable the rule that holds them and restart")
        super().__init__(f"refusing to start: run {run_id} holds multi-day sleeves ({', '.join(self.symbols)}) and "
                         f"this engine has no daily path; {fix}. Nothing was sent; the broker-held stops stay.")


class FlattenIncomplete(RuntimeError):
    """flatten_all tried every position and some are still open. `left` is {symbol: why}; the message names the
    symbols first, so they survive wherever the text is cut (heartbeat, engine_error event, halt reason)."""

    def __init__(self, reason: str, left: dict[str, str]):
        self.reason, self.left = reason, dict(left)
        why = "; ".join(f"{s}: {w}" for s, w in left.items())
        super().__init__(f"flatten ({reason}) incomplete, still open: {', '.join(left)} ({why})")


@dataclass
class Pos:
    trade_id: int
    symbol: str
    qty: int
    coid: str
    order_id: str
    ref: float
    risk_ps: float
    atr: float
    sigma: float
    stop: float
    target: float
    status: str = "pending"  # pending -> open -> exiting -> closed
    fill: float | None = None
    shadow_entry: float | None = None
    stop_order_id: str | None = None
    target_order_id: str | None = None
    exit_order_id: str | None = None
    exit_reason: str | None = None
    armed: bool = False
    hi_close: float = -1e18
    db_order_id: int | None = None
    entry_ts: str | None = None
    carried: bool = False  # left over from an earlier session
    strategy: str = registry.DEFAULT_LIVE[0]

    def features(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in ("trade_id", "symbol", "qty")}

    @property
    def key(self) -> tuple[str, str]:
        return self.strategy, self.symbol


class Positions:
    """The engine's open positions, keyed by (strategy, symbol) (S77): two rules can hold the same stock.

    It still answers by symbol where a question is about the stock, not the rule: `"AAPL" in pos` (any rule holds
    it), `pos["AAPL"]` (the one position in it; ambiguous if two rules hold it), iteration yields symbols and
    `items()` (symbol, position) pairs, in the order the positions were opened."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], Pos] = {}

    def add(self, p: Pos) -> None:
        self._d[p.key] = p

    def pop(self, key, default=None):
        if isinstance(key, Pos):
            key = key.key
        if isinstance(key, str):
            key = next((k for k in self._d if k[1] == key), None)
        return self._d.pop(key, default) if key is not None else default

    def sleeves(self):
        return self._d.items()

    def on(self, symbol: str) -> list[Pos]:
        return [p for (_, s), p in self._d.items() if s == symbol]

    def __getitem__(self, key) -> Pos:
        if isinstance(key, tuple):
            return self._d[key]
        found = self.on(key)
        if len(found) != 1:
            raise KeyError(key if not found else f"{key} is held by {len(found)} rules: ask by (strategy, symbol)")
        return found[0]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key) -> bool:
        return key in self._d if isinstance(key, tuple) else any(s == key for _, s in self._d)

    def __iter__(self):
        return (s for _, s in self._d)

    def items(self):
        return [(s, p) for (_, s), p in self._d.items()]

    def values(self):
        return list(self._d.values())

    def __len__(self) -> int:
        return len(self._d)

    def __bool__(self) -> bool:
        return bool(self._d)

    def __eq__(self, other) -> bool:
        if isinstance(other, Positions):
            return self._d == other._d
        if isinstance(other, dict):
            return dict(self.items()) == other and len(other) == len(self._d)
        return NotImplemented

    def __repr__(self) -> str:
        return f"Positions({ {f'{k[0]}|{k[1]}': p.status for k, p in self._d.items()} })"


@dataclass
class Day:
    run_id: int
    day: date
    open_utc: datetime
    n_min: int
    start_equity: float
    realized_venue: float = 0.0
    realized_shadow: float = 0.0
    realized_reported: float = 0.0
    entries: int = 0
    trades_closed: int = 0
    losses_in_row: int = 0
    pause_until: int = -1
    halted: bool = False
    flattened: bool = False
    finalized: bool = False
    last_m: int = -1
    done_m: int = -1  # S74: the last minute whose work completed (last_m: the last one started). See Engine.tick
    # S74: {strategy: the last minute whose entry decision finished or acted}; a retry never decides it again
    decided: dict = field(default_factory=dict)
    rejects: int = 0  # S74: entry orders the broker refused in a row; an accepted order resets it
    events: list = field(default_factory=list)
    rules: Rules | None = None  # M5: the owner's config for this session (None before the open)
    refused: str | None = None  # M5: entries refused for this session only (invalid config); next day re-checks
    custom_ok: frozenset = frozenset()  # S68: custom tickers that passed today's point-in-time check
    scan: dict = field(default_factory=lambda: new_scan())  # dashboard-only counters, never read by decisions
    # S77: what moves today's equity besides realised and unrealised trade P&L: dividends booked today, and for a
    # position held over the last close and closed today, minus its gain to that close (already in start_equity)
    adjust: float = 0.0

    @property
    def flatten_m(self) -> int:
        return self.n_min - 5  # 15:55 on a normal day, 12:55 on an early close

    @property
    def last_entry_m(self) -> int:
        return self.n_min - 15  # 15:45


def _brief(v) -> str:
    """A command's own value for a log line or an event: its text, cut short (it is not trusted)."""
    return repr(v)[:120]


def _refused(err: Exception) -> bool:
    """S74: the venue answered and said no (HTTP 4xx), as opposed to not answering (network error, 5xx: the
    adapter raises those without a 4xx status after its own retries)."""
    status = getattr(err, "status", None)
    return isinstance(status, int) and 400 <= status < 500


class ExDateSourceMissing(RuntimeError):
    """A multi-day rule on an engine without an ex-date dividend source (S78, #118: accrual at the ex-date)."""


def _holding(strategy: str | None):
    s = registry.get(strategy) if strategy else None
    return getattr(s, "holding", Holding.INTRADAY)


def _et():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")


def _parse_ts(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _clock_ts(v) -> datetime | None:
    """The broker clock's `timestamp` (Alpaca: RFC 3339 with up to nine decimals and an offset) as an aware
    datetime; None when it is missing, unreadable or has no offset."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)", str(v or "").strip())
    if not m:
        return None
    try:
        t = datetime.fromisoformat(m[1] + "." + (m[2] or "0")[:6].ljust(6, "0") + ("+00:00" if m[3] == "Z" else m[3]))
    except ValueError:
        return None
    return t if t.tzinfo else None


def _iso(v) -> str | None:
    return None if v is None else _parse_ts(v).astimezone(UTC).isoformat()


def _close_ts(session: str | None) -> str | None:
    """16:00 ET of a session (the time a close mark is from), as UTC ISO; None without a session."""
    if not session:
        return None
    try:
        d = date.fromisoformat(session)
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, 16, tzinfo=_et()).astimezone(UTC).isoformat()


def _et_date(v) -> date | None:
    return None if v is None else _parse_ts(v).astimezone(_et()).date()


def _num(v):
    v = float(v)
    return None if not np.isfinite(v) else round(v, 6)


def new_idempotency_key() -> str:
    return uuid.uuid4().hex


def _posted_since(row: dict, since: datetime) -> bool:
    """#238: did this fee reach the broker's cash after `since` (the run's cash baseline)? By its `created_at`
    when Alpaca gives one (a fee for the trade day before the run, posted after the baseline, counts), else by its
    date (from the baseline's day on)."""
    created = row.get("created_at")
    if created:
        t = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=UTC)) >= since
    return str(row.get("date") or "")[:10] >= since.astimezone(UTC).date().isoformat()
