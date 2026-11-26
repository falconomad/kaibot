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

The upcoming-earnings calendar (display only, migration 0054): once a UTC day, BEFORE the backfill, ONE request of
`function=EARNINGS_CALENDAR&horizon=3month` (a CSV of every US symbol's next reports) inside the same daily budget,
so the backfill gets the rest (19 of 20). Rows are kept for the engine universe only, cached as
calendar/<day>.csv and handed to kaibot.earnings_calendar_refresh (service key, never printed). The engine never
reads the calendar: no decision may use it before the pre-registered filter of decision 20 passes gate v3.1.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
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
    last_request_at: str = ""     # UTC ISO time of the last request sent (the 13 s gap holds across runs)
    calendar_day: str = ""        # the UTC day the calendar request was last answered with a CSV
    calendar_pushed_day: str = ""  # the UTC day its rows last reached the database
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


def _since_last(state: State, at: datetime, gap_s: float) -> float:
    """Seconds still to wait so that the gap since the last request (any run, any function) is at least gap_s."""
    if not state.last_request_at:
        return 0.0
    gone = (at.astimezone(UTC) - datetime.fromisoformat(state.last_request_at)).total_seconds()
    return max(0.0, gap_s - gone) if gone >= 0 else gap_s


def spend(state: State, budget: int) -> bool:
    """Take one request of today's budget; False when the day's budget or the day itself is used up."""
    if state.stopped_today or state.requests_today >= budget:
        return False
    state.requests_today += 1
    return True


def backfill(symbols: Iterable[str], root: Path, get: Callable[[dict], object], *, budget: int = DEFAULT_BUDGET,
             now: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], None] | None = None,
             gap_s: float = MIN_GAP_S, refresh_days: int = REFRESH_DAYS,
             reserve_calendar: bool = False) -> tuple[RunResult, State]:
    """Ask EARNINGS for the planned symbols until the day's budget is spent or AV says stop. `reserve_calendar`:
    keep one request of the day for the calendar while it has not been fetched that day."""
    budget = clamp_budget(budget)
    sleep = sleep or time.sleep  # looked up at call time (tests replace time.sleep)
    gap_s = max(gap_s, MIN_GAP_S)
    state_path = root / "state.json"
    state = State.load(state_path)
    state.roll(utc_day(now()))
    if reserve_calendar and state.calendar_day != state.day:
        budget = max(0, budget - CALENDAR_RESERVE)
    res = RunResult()
    first = True
    for sym in plan(symbols, state, now(), refresh_days):
        if not spend(state, budget):
            res.stopped = res.stopped or ("rate_limit" if state.stopped_today == "rate_limit" else "budget")
            break
        wait = gap_s if not first else _since_last(state, now(), gap_s)
        if wait > 0:
            sleep(wait)
        first = False
        state.last_request_at = now().astimezone(UTC).isoformat()
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


# ---------------------------------------------------------------- the upcoming-earnings calendar (display only)

CALENDAR_RESERVE = 1
CALENDAR_PARAMS = {"function": "EARNINGS_CALENDAR", "horizon": "3month"}
_TIMES = {"pre-market": "pre-market", "post-market": "post-market", "bmo": "pre-market", "amc": "post-market"}


def _iso(v: str) -> str | None:
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        return None


def parse_calendar_csv(text: str, universe: Iterable[str]) -> list[dict]:
    """The calendar CSV (symbol,name,reportDate,fiscalDateEnding,estimate,currency[,timeOfTheDay]) -> rows of the
    universe's symbols (AV's BRK-B is the universe's BRK.B), one per (symbol, fiscal_date_ending), the earliest
    report date kept. A blank estimate or time is None (the card omits it)."""
    uni = {s.upper(): s for s in universe}
    uni_dash = {av_symbol(s): s for s in uni.values()}
    out: dict[tuple[str, str], dict] = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        raw = str(r.get("symbol") or "").strip().upper()
        sym = uni.get(raw) or uni_dash.get(raw)
        rd, fde = _iso(r.get("reportDate") or ""), _iso(r.get("fiscalDateEnding") or "")
        if not sym or not rd or not fde:
            continue
        est = _num_or_none(r.get("estimate"))
        cur = str(r.get("currency") or "").strip().upper()
        tod = _TIMES.get(str(r.get("timeOfTheDay") or r.get("reportTime") or "").strip().lower())
        row = {"symbol": sym, "report_date": rd, "fiscal_date_ending": fde, "report_time": tod, "estimate": est,
               "currency": cur if len(cur) == 3 and cur.isalpha() else None}
        k = (sym, fde)
        if k not in out or rd < out[k]["report_date"]:
            out[k] = row
    return sorted(out.values(), key=lambda x: (x["report_date"], x["symbol"]))


def _num_or_none(v) -> float | None:
    s = str(v if v is not None else "").strip()
    if s in ("", "None", "-", "N/A"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f == f and abs(f) != float("inf") else None


@dataclass
class CalendarResult:
    status: str               # "fetched", "cached" (already today), "skipped: ...", "rate_limit", "key", "error: ..."
    rows: list[dict] = field(default_factory=list)
    exit_code: int = 0


def calendar_path(root: Path, day: str) -> Path:
    return root / "calendar" / f"{day}.csv"


def fetch_calendar(universe: Iterable[str], root: Path, get: Callable[[dict], object], *,
                   budget: int = DEFAULT_BUDGET, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                   sleep: Callable[[float], None] | None = None) -> tuple[CalendarResult, State]:
    """Once a UTC day: one EARNINGS_CALENDAR request from the day's budget. Later the same day the cached CSV is
    read again (no request), so a failed database write can be retried for free."""
    budget = clamp_budget(budget)
    sleep = sleep or time.sleep
    state_path = root / "state.json"
    state = State.load(state_path)
    state.roll(utc_day(now()))
    universe = list(universe)
    if state.calendar_day == state.day and calendar_path(root, state.day).is_file():
        rows = parse_calendar_csv(calendar_path(root, state.day).read_text(), universe)
        return CalendarResult("cached", rows), state
    if not spend(state, budget):
        return CalendarResult("rate_limit" if state.stopped_today == "rate_limit" else "skipped: budget"), state
    wait = _since_last(state, now(), MIN_GAP_S)
    if wait > 0:
        sleep(wait)
    state.last_request_at = now().astimezone(UTC).isoformat()
    state.save(state_path)
    try:
        answer = get(dict(CALENDAR_PARAMS))
    except Exception as e:  # noqa: BLE001 - the request counted; the backfill may still run
        state.save(state_path)
        return CalendarResult(f"error: {str(e)[:200]}", exit_code=1), state
    if not isinstance(answer, str):
        kind = classify(answer)
        res = CalendarResult("error: unexpected JSON answer", exit_code=1)
        if kind == "limit":
            state.stopped_today, res = "rate_limit", CalendarResult("rate_limit")
        elif kind == "key":
            state.stopped_today, res = "key", CalendarResult("key", exit_code=2)
        state.save(state_path)
        return res, state
    header = answer.lstrip("\ufeff").splitlines()[0] if answer.strip() else ""
    if "reportDate" not in header:
        state.save(state_path)
        return CalendarResult("error: not the calendar CSV", exit_code=1), state
    _write_atomic(calendar_path(root, state.day), answer)
    for old in sorted((root / "calendar").glob("*.csv"))[:-30]:  # keep a month of daily answers
        old.unlink(missing_ok=True)
    state.calendar_day = state.day
    state.save(state_path)
    return CalendarResult("fetched", parse_calendar_csv(answer, universe)), state


def push_calendar(rows: list[dict], fetched_at: datetime, store) -> dict:
    """Upsert the rows and prune past report dates in one call (kaibot.earnings_calendar_refresh, migration 0054)."""
    return store.rpc("earnings_calendar_refresh",
                     {"p_rows": rows, "p_fetched_at": fetched_at.astimezone(UTC).isoformat(timespec="seconds")})


def mark_pushed(root: Path, day: str) -> None:
    p = root / "state.json"
    st = State.load(p)
    st.calendar_pushed_day = day
    st.save(p)

