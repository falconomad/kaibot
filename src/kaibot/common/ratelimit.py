"""Per-source request budgets so free-tier limits are never exceeded (story S17).

Every outgoing call acquires from its source's `Budget` first. Budgets come from config/sources.yaml
(`budget_per_s`, `budget_per_min`, `budget_per_hour`, `min_seconds_between_requests`), not from code.
`get_budget(source)` returns ONE process-wide budget per source, so two clients of the same source
(e.g. the engine's AlpacaData and the history downloader) share it.

The budget is synchronous and thread-safe. Each window is a sliding log of grant times, so in ANY interval of
`per_seconds` at most `capacity` calls are granted (a token bucket would allow up to 2x at a window boundary).
A caller reserves the earliest slot that satisfies every window, the minimum gap and any shared backoff, then
sleeps outside the lock until that slot. Budgets are per process: separate processes (parallel workers, the
nightly collector next to the engine) each get their own, which is why sources.yaml budgets stay well below
the provider limits.

Audit 2 ticket 7.6 (#716): a source with `shared_budget_per_s` in sources.yaml (sec_edgar: 8, under SEC's 10 a
second per IP) also takes a slot from a window shared by every process on the machine: `SharedWindow`, a small
file under an exclusive flock in data/ratelimit/<source>.slots (KAIBOT_RATELIMIT_DIR elsewhere; the research
runner points its jobs at the engine checkout's). Each process keeps its own per-process budget as well. The shared
window counts wall-clock time (monotonic clocks differ between processes), applies only to budgets on the real
clock (a test's fake clock never touches the file), and a 429's backoff holds every process. If the file cannot be
used, the process says so once and keeps only its own budget: requests are never blocked by a broken lock file.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

try:
    import fcntl
except ImportError:  # not POSIX: no shared window (the engine runs on macOS or Linux)
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[3]
SOURCES_FILE = REPO / "config" / "sources.yaml"
SHARED_DIR = REPO / "data" / "ratelimit"
SHARED_AHEAD_S = 300.0  # a stored slot further ahead than this is a clock jump, not a queue: dropped
SHARED_BACKOFF_MAX_S = 60.0  # as common.http caps Retry-After
_WINDOWS = {"budget_per_s": 1.0, "budget_per_min": 60.0, "budget_per_hour": 3600.0}


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


@dataclass(frozen=True)
class BudgetSpec:
    """`windows` is a tuple of (capacity, per_seconds); `min_interval` is the minimum gap between calls;
    `shared_per_s` is the per-second cap for all processes on the machine together (None: per process only)."""

    windows: tuple[tuple[int, float], ...] = ()
    min_interval: float = 0.0
    shared_per_s: int | None = None

    def __post_init__(self) -> None:
        for cap, per in self.windows:
            if cap <= 0 or per <= 0:
                raise ValueError("capacity and per_seconds must be positive")
        if self.min_interval < 0 or not math.isfinite(self.min_interval):
            raise ValueError("min_interval must be a finite number >= 0")
        if self.shared_per_s is not None and (isinstance(self.shared_per_s, bool) or self.shared_per_s <= 0):
            raise ValueError("shared_per_s must be a positive integer")

    @property
    def unlimited(self) -> bool:
        return not self.windows and self.min_interval == 0

    @classmethod
    def from_mapping(cls, d: dict) -> BudgetSpec:
        windows = []
        for key, per in _WINDOWS.items():
            if d.get(key) is not None:
                v = d[key]
                if isinstance(v, bool) or not isinstance(v, int | float) or v <= 0 or int(v) != v:
                    raise ValueError(f"{key} must be a positive integer, got {v!r}")
                windows.append((int(v), per))
        gap = d.get("min_seconds_between_requests")
        if gap is not None and (isinstance(gap, bool) or not isinstance(gap, int | float) or gap < 0):
            raise ValueError(f"min_seconds_between_requests must be a number >= 0, got {gap!r}")
        shared = d.get("shared_budget_per_s")
        if shared is not None and (isinstance(shared, bool) or not isinstance(shared, int | float) or shared <= 0
                                   or int(shared) != shared):
            raise ValueError(f"shared_budget_per_s must be a positive integer, got {shared!r}")
        return cls(tuple(windows), float(gap or 0.0), int(shared) if shared is not None else None)


def shared_dir() -> Path:
    """Where the shared windows live: KAIBOT_RATELIMIT_DIR, else data/ratelimit of this checkout."""
    from kaibot.common import switches

    d = switches.get("KAIBOT_RATELIMIT_DIR")
    return Path(d).expanduser() if d else SHARED_DIR


class SharedWindow:
    """At most `capacity` grants in any `per` seconds across every process that uses the same file.

    The file holds the last `capacity` granted slots (wall-clock seconds) and a backoff time, as JSON. A caller takes
    the flock, books the earliest slot that keeps the window and is at least per/capacity after the last one, writes
    the file, lets go of the lock, then sleeps until its slot. Slots only move forward, so the file stays sorted."""

    def __init__(self, path: Path, capacity: int, per: float = 1.0, wall=time.time, sleep=time.sleep):
        if capacity <= 0 or per <= 0:
            raise ValueError("capacity and per must be positive")
        self.path, self.capacity, self.per = Path(path), int(capacity), float(per)
        self.wall, self.sleep = wall, sleep
        self.broken: str | None = None  # why the file cannot be used (then only the process's own budget holds)
        self.waited = 0.0

    def _update(self, change) -> float | None:
        """Run `change(slots, blocked, now) -> (slots, blocked, result)` under the file's lock."""
        if fcntl is None:
            self.broken = "no fcntl on this platform"
            return None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            self._fail(f"{type(e).__name__}: {e}")
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            raw = b""
            while chunk := os.read(fd, 65536):
                raw += chunk
            now = self.wall()
            try:
                doc = json.loads(raw) if raw.strip() else {}
                slots = [float(x) for x in doc.get("slots", [])]
                blocked = float(doc.get("blocked_until", 0.0))
            except (ValueError, TypeError, AttributeError):
                slots, blocked = [], 0.0  # a damaged file starts again (it holds no state worth keeping)
            # an older slot cannot bind a new one (>= now); a slot or a backoff far ahead is a clock jump
            slots = sorted(x for x in slots if now - self.per < x <= now + SHARED_AHEAD_S)
            if not math.isfinite(blocked) or blocked > now + SHARED_BACKOFF_MAX_S:
                blocked = now
            slots, blocked, result = change(slots, blocked, now)
            data = json.dumps({"slots": slots[-self.capacity:], "blocked_until": blocked}).encode()
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, data)
            return result
        except OSError as e:
            self._fail(f"{type(e).__name__}: {e}")
            return None
        finally:
            os.close(fd)  # closing the descriptor lets go of the flock

    def _fail(self, why: str) -> None:
        if self.broken is None:
            log.warning("shared rate limit %s unusable (%s): this process keeps only its own budget", self.path, why)
        self.broken = why

    def reserve(self) -> float | None:
        """Book the next shared slot (wall-clock time) without sleeping; None when the file cannot be used."""

        def book(slots, blocked, now):
            # evenly spaced, per/capacity apart (8 a second: 125 ms): a window packed into bursts at its edges would
            # let a few ms of wake-up or network jitter put two bursts into one of SEC's seconds
            t = max(now, blocked, slots[-1] + self.per / self.capacity if slots else now)
            if len(slots) >= self.capacity:
                t = max(t, slots[-self.capacity] + self.per)
            return [*slots, t], blocked, t

        return self._update(book)

    def acquire(self) -> float | None:
        t = self.reserve()
        if t is not None:
            wait = t - self.wall()
            if wait > 0:
                self.waited += wait
                self.sleep(wait)
        return t

    def backoff(self, seconds: float) -> None:
        """Hold every process's next slot for `seconds` from now (a 429)."""
        hold = min(max(0.0, seconds), SHARED_BACKOFF_MAX_S)
        self._update(lambda slots, blocked, now: (slots, max(blocked, now + hold), None))


class Budget:
    """Thread-safe sliding-window budget with a minimum gap and a shared backoff (used after a 429)."""

    def __init__(self, name: str, spec: BudgetSpec, clock: Clock | None = None, shared: SharedWindow | None = None):
        self.name = name
        self.spec = spec
        self.clock = clock or SystemClock()
        self.shared = shared  # the machine-wide window (#716), on top of this process's own budget
        self._lock = threading.Lock()
        self._grants = [deque(maxlen=cap) for cap, _ in spec.windows]
        self._last = -math.inf
        self._blocked_until = -math.inf
        self.waited = 0.0  # total seconds callers were held back (observability)

    def reserve(self, latest: float | None = None) -> float | None:
        """Book the next allowed slot and return its time on the budget's clock (no sleeping). With `latest`, a
        slot after that time is not booked and None is returned, so a caller that gives up on it (an order-path
        deadline, S74d #154) does not hold back the other callers of the source."""
        with self._lock:
            t = max(self.clock.monotonic(), self._blocked_until, self._last + self.spec.min_interval)
            for (cap, per), dq in zip(self.spec.windows, self._grants, strict=True):
                if len(dq) == cap:
                    t = max(t, dq[0] + per)
            if latest is not None and t > latest:
                return None
            for dq in self._grants:
                dq.append(t)
            self._last = t
            return t

    def acquire(self) -> float:
        """Block until this caller may send one request; returns the granted slot time."""
        if self.spec.unlimited and self._blocked_until == -math.inf and self.shared is None:
            return self.clock.monotonic()
        t = self.reserve()
        wait = t - self.clock.monotonic()
        if wait > 0:
            self.waited += wait
            self.clock.sleep(wait)
        if self.shared is not None:
            before = self.clock.monotonic()
            self.shared.acquire()
            t = max(t, self.clock.monotonic())
            self.waited += max(0.0, t - before)
        return t

    def backoff(self, seconds: float) -> None:
        """Hold every caller of this source for `seconds` from now (provider said 429: slow down together)."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self.clock.monotonic() + max(0.0, seconds))
        if self.shared is not None:
            self.shared.backoff(seconds)


def load_specs(path: str | Path | None = None) -> dict[str, BudgetSpec]:
    """Flatten sources.yaml (sections -> sources) into {source_name: BudgetSpec}. Raises on malformed budgets."""
    from kaibot.common import switches  # M10 1.23 (#448)

    p = Path(path or switches.get("KAIBOT_SOURCES_FILE") or SOURCES_FILE)
    doc = yaml.safe_load(p.read_text()) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: expected a mapping of sections")
    out: dict[str, BudgetSpec] = {}
    for section, sources in doc.items():
        if not isinstance(sources, dict):
            raise ValueError(f"{p}: section {section!r} must be a mapping")
        for name, d in sources.items():
            if not isinstance(d, dict):
                continue
            try:
                out[str(name)] = BudgetSpec.from_mapping(d)
            except ValueError as e:
                raise ValueError(f"{p}: {section}.{name}: {e}") from None
    return out


class Registry:
    """One Budget per source name for the whole process."""

    def __init__(self, loader: Callable[[], dict[str, BudgetSpec]] = load_specs, clock: Clock | None = None):
        self._loader = loader
        self._clock = clock
        self._specs: dict[str, BudgetSpec] | None = None
        self._budgets: dict[str, Budget] = {}
        self._lock = threading.Lock()

    def get(self, source: str) -> Budget:
        with self._lock:
            b = self._budgets.get(source)
            if b is None:
                if self._specs is None:
                    self._specs = self._loader()
                if source not in self._specs:
                    raise KeyError(f"no budget for source {source!r}: add it to config/sources.yaml")
                spec = self._specs[source]
                b = self._budgets[source] = Budget(source, spec, self._clock, self._shared(source, spec, self._clock))
            return b

    @staticmethod
    def _shared(source: str, spec: BudgetSpec, clock: Clock | None) -> SharedWindow | None:
        """The machine-wide window for a source that has one, on the real clock only (a fake clock is a test's)."""
        if spec.shared_per_s is None or clock is not None:
            return None
        return SharedWindow(shared_dir() / f"{source}.slots", spec.shared_per_s)

    def configure(self, source: str, spec: BudgetSpec, clock: Clock | None = None) -> Budget:
        """Replace a source's budget (tests, or a script that wants a tighter per-worker budget)."""
        with self._lock:
            c = clock or self._clock
            b = self._budgets[source] = Budget(source, spec, c, self._shared(source, spec, c))
            return b

    def tighten(self, source: str, extra: BudgetSpec) -> Budget:
        """Add limits to a source's configured budget (never loosens it: every window must still hold)."""
        base = self.get(source).spec
        shared = [x for x in (base.shared_per_s, extra.shared_per_s) if x is not None]
        spec = BudgetSpec(base.windows + extra.windows, max(base.min_interval, extra.min_interval),
                          min(shared) if shared else None)
        return self.configure(source, spec)

    def reset(self) -> None:
        with self._lock:
            self._budgets.clear()
            self._specs = None


REGISTRY = Registry()


def get_budget(source: str) -> Budget:
    return REGISTRY.get(source)


@dataclass
class TokenBucket:
    """Simple non-blocking token bucket (kept for callers that only need try_acquire). Thread-safe."""

    capacity: int
    per_seconds: float
    clock: Clock = field(default_factory=SystemClock)
    _tokens: float = field(init=False)
    _updated: float = field(init=False)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.capacity <= 0 or self.per_seconds <= 0:
            raise ValueError("capacity and per_seconds must be positive")
        self._tokens = float(self.capacity)
        self._updated = self.clock.monotonic()

    @property
    def rate(self) -> float:
        return self.capacity / self.per_seconds

    def try_acquire(self, n: int = 1) -> bool:
        with self._lock:
            now = self.clock.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False
