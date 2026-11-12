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
"""

from __future__ import annotations

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

SOURCES_FILE = Path(__file__).resolve().parents[3] / "config" / "sources.yaml"
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
    """`windows` is a tuple of (capacity, per_seconds); `min_interval` is the minimum gap between calls."""

    windows: tuple[tuple[int, float], ...] = ()
    min_interval: float = 0.0

    def __post_init__(self) -> None:
        for cap, per in self.windows:
            if cap <= 0 or per <= 0:
                raise ValueError("capacity and per_seconds must be positive")
        if self.min_interval < 0 or not math.isfinite(self.min_interval):
            raise ValueError("min_interval must be a finite number >= 0")

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
        return cls(tuple(windows), float(gap or 0.0))


class Budget:
    """Thread-safe sliding-window budget with a minimum gap and a shared backoff (used after a 429)."""

    def __init__(self, name: str, spec: BudgetSpec, clock: Clock | None = None):
        self.name = name
        self.spec = spec
        self.clock = clock or SystemClock()
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
        if self.spec.unlimited and self._blocked_until == -math.inf:
            return self.clock.monotonic()
        t = self.reserve()
        wait = t - self.clock.monotonic()
        if wait > 0:
            self.waited += wait
            self.clock.sleep(wait)
        return t

    def backoff(self, seconds: float) -> None:
        """Hold every caller of this source for `seconds` from now (provider said 429: slow down together)."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self.clock.monotonic() + max(0.0, seconds))


def load_specs(path: str | Path | None = None) -> dict[str, BudgetSpec]:
    """Flatten sources.yaml (sections -> sources) into {source_name: BudgetSpec}. Raises on malformed budgets."""
    p = Path(path or os.environ.get("KAIBOT_SOURCES_FILE") or SOURCES_FILE)
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
                b = self._budgets[source] = Budget(source, self._specs[source], self._clock)
            return b

    def configure(self, source: str, spec: BudgetSpec, clock: Clock | None = None) -> Budget:
        """Replace a source's budget (tests, or a script that wants a tighter per-worker budget)."""
        with self._lock:
            b = self._budgets[source] = Budget(source, spec, clock or self._clock)
            return b

    def tighten(self, source: str, extra: BudgetSpec) -> Budget:
        """Add limits to a source's configured budget (never loosens it: every window must still hold)."""
        base = self.get(source).spec
        spec = BudgetSpec(base.windows + extra.windows, max(base.min_interval, extra.min_interval))
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
