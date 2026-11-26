"""Alpha Vantage, free tier only (stakeholder, 26 Nov 2026): the EARNINGS backfill for research (decision 20).

Free tier: 25 requests a day and 5 a minute per key, and the owner uses the same key elsewhere. So this module
keeps its own daily count in a small state file and never spends more than `budget` requests a day (default 20,
never more than HARD_MAX_PER_DAY = 23), waits at least MIN_GAP_S = 13 s between two calls (4.6 a minute), and
stops at once when Alpha Vantage answers with its rate-limit text ("Information" or "Note"): that answer is never
stored as data, and no further request is sent that day.

The key is ALPHAVANTAGE_API_KEY, read (only that one name) from data.env (~/Projects/kaibot-secrets/data.env, or
the file KAIBOT_DATA_ENV_FILE names), else from the --env file, else from the environment. No key: the caller
prints a warning and exits 0. The key is sent as the `apikey` query value, which kaibot.common.http redacts in
every log and error line; nothing here prints it.

Cache (data/earnings/alphavantage/, not in git):
  raw/<SYMBOL>.json   the raw EARNINGS answer, as received
  state.json          day, requests that day, why the day stopped, per-symbol fetch times, symbols without data
Order: the symbols given (the engine universe, alphabetical), every uncached symbol first; only once every symbol is
cached (or known to have no data) are cached symbols older than REFRESH_DAYS re-read, oldest first.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kaibot.common.secret_files import data_env_path, read_names

log = logging.getLogger(__name__)

KEY_NAME = "ALPHAVANTAGE_API_KEY"
SOURCE = "alphavantage"
URL = "https://www.alphavantage.co/query"
DEFAULT_BUDGET = 20
HARD_MAX_PER_DAY = 23  # 25 free minus a margin for the owner's own use of the key
MIN_GAP_S = 13.0
REFRESH_DAYS = 100
STOP_KEYS = ("Information", "Note")


class RateLimited(Exception):
    """Alpha Vantage answered with its limit text: stop for the day."""


class KeyRejected(Exception):
    """The key is missing or invalid according to Alpha Vantage."""


def api_key(env_file: str | None = None, data_file: str | None = None) -> str:
    """ALPHAVANTAGE_API_KEY from data.env, else the --env file, else the environment; "" when there is none."""
    for p in (data_env_path(data_file), env_file):
        if p:
            v = read_names(p, [KEY_NAME]).get(KEY_NAME, "")
            if v:
                return v
    return os.environ.get(KEY_NAME, "").strip()


def av_symbol(symbol: str) -> str:
    """Alpha Vantage writes share classes with a dash (BRK-B)."""
    return symbol.replace(".", "-")


def classify(payload) -> str:
    """'data', 'limit', 'key' (key problem), or 'none' (no earnings for the symbol)."""
    if not isinstance(payload, dict):
        return "none"
    if any(k in payload for k in STOP_KEYS):
        return "limit"
    err = str(payload.get("Error Message") or "")
    if err:
        return "key" if "apikey" in err.lower() else "none"
    if isinstance(payload.get("quarterlyEarnings"), list) and payload["quarterlyEarnings"]:
        return "data"
    return "none"


def http_getter(key: str, timeout: float = 30.0) -> Callable[[dict], object]:
    """One GET per call (attempts=1: every attempt costs a request of the daily quota), under the `alphavantage`
    budget of config/sources.yaml. Returns the parsed JSON, or the CSV text when `datatype` is csv."""
    from kaibot.common import http

    def get(params: dict):
        r = http.get(URL, source=SOURCE, params={**params, "apikey": key}, timeout=timeout, attempts=1,
                     max_bytes=8 * 1024 * 1024)
        text = r.body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except ValueError:
            return text

    return get


# ---------------------------------------------------------------- state


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


@dataclass
class State:
    day: str = ""
    requests_today: int = 0
    stopped_today: str = ""
    fetched: dict[str, str] = field(default_factory=dict)   # symbol -> UTC ISO time of the cached answer
    missing: dict[str, str] = field(default_factory=dict)   # symbol -> UTC ISO time AV said "no data"
    history: list[dict] = field(default_factory=list)       # one line per run (last 60)

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.is_file():
            return cls()
        d = json.loads(path.read_text())
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def save(self, path: Path) -> None:
        self.history = self.history[-60:]
        _write_atomic(path, json.dumps(self.__dict__, indent=1, sort_keys=True))

    def roll(self, today: str) -> None:
        if self.day != today:
            self.day, self.requests_today, self.stopped_today = today, 0, ""


def utc_day(now: datetime) -> str:
    """The quota day. Alpha Vantage does not say when its day turns; the UTC date is used."""
    return now.astimezone(UTC).date().isoformat()


def clamp_budget(budget: int) -> int:
    if budget < 0 or budget > HARD_MAX_PER_DAY:
        raise ValueError(f"budget must be 0..{HARD_MAX_PER_DAY} requests a day, got {budget}")
    return budget


def plan(symbols: Iterable[str], state: State, now: datetime, refresh_days: int = REFRESH_DAYS) -> list[str]:
    """What to ask for, in order: uncached symbols (alphabetical); then, only when none is left, cached or
    no-data symbols older than `refresh_days`, oldest first."""
    syms = sorted(set(symbols))
    todo = [s for s in syms if s not in state.fetched and s not in state.missing]
    if todo:
        return todo
    cutoff = now.astimezone(UTC) - timedelta(days=refresh_days)
    seen = {**state.missing, **state.fetched}
    stale = [s for s in syms if datetime.fromisoformat(seen[s]) < cutoff]
    return sorted(stale, key=lambda s: (seen[s], s))


@dataclass
class RunResult:
    used_this_run: int = 0
    cached: list[str] = field(default_factory=list)
    no_data: list[str] = field(default_factory=list)
    stopped: str = ""         # "", "budget", "rate_limit", "key", "error: ..."
    exit_code: int = 0


def spend(state: State, budget: int) -> bool:
    """Take one request of today's budget; False when the day's budget or the day itself is used up."""
    if state.stopped_today or state.requests_today >= budget:
        return False
    state.requests_today += 1
    return True


def backfill(symbols: Iterable[str], root: Path, get: Callable[[dict], object], *, budget: int = DEFAULT_BUDGET,
             now: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], None] = time.sleep,
             gap_s: float = MIN_GAP_S, refresh_days: int = REFRESH_DAYS) -> tuple[RunResult, State]:
    """Ask EARNINGS for the planned symbols until the day's budget is spent or AV says stop."""
    budget = clamp_budget(budget)
    gap_s = max(gap_s, MIN_GAP_S)
    state_path = root / "state.json"
    state = State.load(state_path)
    state.roll(utc_day(now()))
    res = RunResult()
    first = True
    for sym in plan(symbols, state, now(), refresh_days):
        if not spend(state, budget):
            res.stopped = res.stopped or ("rate_limit" if state.stopped_today == "rate_limit" else "budget")
            break
        if not first:
            sleep(gap_s)
        first = False
        res.used_this_run += 1
        state.save(state_path)  # the request counts before it is sent: a crash never under-counts
        try:
            payload = get({"function": "EARNINGS", "symbol": av_symbol(sym)})
        except Exception as e:  # noqa: BLE001 - network or HTTP error: the request counted; stop the run
            res.stopped, res.exit_code = f"error: {str(e)[:200]}", 1
            break
        kind = classify(payload)
        if kind == "limit":
            state.stopped_today, res.stopped = "rate_limit", "rate_limit"
            break
        if kind == "key":
            state.stopped_today, res.stopped, res.exit_code = "key", "key", 2
            break
        stamp = now().astimezone(UTC).isoformat(timespec="seconds")
        if kind == "data":
            _write_atomic(root / "raw" / f"{sym}.json", json.dumps(payload))
            state.fetched[sym] = stamp
            state.missing.pop(sym, None)
            res.cached.append(sym)
        else:
            state.missing[sym] = stamp
            res.no_data.append(sym)
    state.history.append({"at": now().astimezone(UTC).isoformat(timespec="seconds"), "used": res.used_this_run,
                          "cached": len(res.cached), "no_data": len(res.no_data), "stopped": res.stopped})
    state.save(state_path)
    return res, state


def progress(symbols: Iterable[str], state: State, budget: int) -> dict:
    syms = set(symbols)
    return {"cached": len(syms & set(state.fetched)), "no_data": len(syms & set(state.missing)),
            "total": len(syms), "requests_today": state.requests_today, "budget_today": budget,
            "day_utc": state.day, "stopped_today": state.stopped_today}
