"""Raw daily bars and corporate actions from Alpaca into a Parquet store (story S84, issue #124, audit E4).

Research data only: the live engine never imports this module and never reads the store.

Why: the minute history is split-adjusted as of the day it was downloaded and carries no dividends. Here the bars
are stored RAW (as traded) next to a table of corporate actions, and `kaibot.ingest.daily_store` adjusts at read
time as of a decision date: signals read adjusted prices, orders and costs read raw prices.

Layout under <data>/daily:
  bars/symbol=<KEY>.parquet      raw daily bars of one company from 2016, one row per session (schema: BARS_SCHEMA)
  bars/symbol=<KEY>.empty        asked, nothing usable came back (JSON says why) -> also listed in holes.csv
  ref_all/symbol=<KEY>.parquet   Alpaca's own `adjustment=all` close and volume as of the same fetch: used only to
                                 check the table (and as a fallback factor), never stored as prices
  corporate_actions.parquet      splits, stock dividends and cash dividends with ex-dates (schema: ACTIONS_SCHEMA);
                                 spin-offs and name changes are kept as information
  holes.csv                      every key Alpaca gave no usable bars for
KEY is the ticker (today's company with that ticker) or TICKER@YYYY-MM-DD for a ticker requested as of a date
inside its life (`--symbols-file` with the header `symbol,asof_date`; the contract with story S83, #123).

Alpaca parameters (checked against docs.alpaca.markets on 12 Nov 2026; see docs/ops/daily_bars.md for what could
not be verified there):
  GET /v2/stocks/{symbol}/bars   timeframe=1Day, adjustment=raw (or all), feed=sip, limit=10000, start, end,
                                 asof=YYYY-MM-DD (only for --symbols-file lines), page_token
  GET /v1/corporate-actions      symbols, types, start, end, limit=1000, page_token
SIP history is free only when `end` is at least 15 minutes old. A daily bar keeps growing after 16:00 ET
(extended-hours trades add to its volume), so a session is stored only once it is complete (20:15 ET).
Every request runs under the shared `alpaca` budget of config/sources.yaml.
"""

from __future__ import annotations

import csv
import json
import math
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from kaibot.common.http import HttpError
from kaibot.ingest.alpaca_history import ET, SIP_DELAY, parse_symbols

STORE = "daily"
START = date(2016, 1, 1)  # Alpaca's history begins in 2016
SCHEMA_VERSION = 1
META_KEY = b"kaibot.daily"
COMPLETE_AT = time(20, 15)  # ET: extended hours end at 20:00 and still add to the day's volume
SKIP_STATUSES = (400, 404, 422)  # Alpaca's answers for a symbol it does not know: log a hole, keep going
MAX_PAGES = 500  # ten years of one symbol are one page, all corporate actions a few dozen: more is a broken token
ASOF_WINDOW_DAYS = 10  # an as-of request must return a bar this close to its date, or it is the wrong company
DEAD_AFTER_DAYS = 10  # a series whose last bar is older than this has ended (delisted, acquired)
FILE_HEADER = ["symbol", "asof_date"]  # the --symbols-file contract with story S83 (#123): do not change

BARS_SCHEMA = pa.schema([
    ("date", pa.date32()),  # session date in New York
    ("o", pa.float64()), ("h", pa.float64()), ("l", pa.float64()), ("c", pa.float64()),  # raw prices, as traded
    ("v", pa.float64()),  # raw shares, extended hours and odd lots included (Alpaca's daily-bar rule)
    ("n", pa.int64()),  # trades
    ("vw", pa.float64()),  # volume-weighted average price, raw
    ("available_ts", pa.timestamp("s", tz="UTC")),  # when the complete bar could first be known: 20:15 ET that day
])
REF_SCHEMA = pa.schema([("date", pa.date32()), ("c", pa.float64()), ("v", pa.float64())])

CA_PATH = "/v1/corporate-actions"
CA_ARRAYS = {"forward_splits": "forward_split", "reverse_splits": "reverse_split",
             "stock_dividends": "stock_dividend", "cash_dividends": "cash_dividend",
             "spin_offs": "spin_off", "name_changes": "name_change"}
CA_TYPES = tuple(CA_ARRAYS.values())
SHARE_TYPES = ("forward_split", "reverse_split", "stock_dividend")  # change the share count: `ratio`
ADJUSTING_TYPES = (*SHARE_TYPES, "cash_dividend")  # what the reader applies to prices
CA_CHUNK = 50  # symbols per request (the documented limit is on rows, 1000 a page; the URL stays short)
CA_PAGE = 1000
CA_FUTURE_DAYS = 120  # announced actions that are not processed yet (a dividend is processed on its pay date)
ACTIONS_SCHEMA = pa.schema([
    ("key", pa.string()),  # store key the action belongs to
    ("symbol", pa.string()),  # symbol in Alpaca's row
    ("type", pa.string()),  # forward_split | reverse_split | stock_dividend | cash_dividend | spin_off | name_change
    ("ex_date", pa.date32()),  # first session on the new basis (name_change: the process date)
    ("ratio", pa.float64()),  # new shares per old share: 4.0 (4-for-1), 0.1 (1-for-10), 1.05 (5% stock dividend)
    ("cash", pa.float64()),  # cash per share (cash_dividend)
    ("special", pa.bool_()), ("foreign", pa.bool_()),
    ("process_date", pa.date32()), ("record_date", pa.date32()), ("payable_date", pa.date32()),
    ("cusip", pa.string()), ("id", pa.string()),
    ("dup", pa.bool_()),  # same key, type, ex-date, amount and special flag as an earlier row: the reader skips it
    ("raw", pa.string()),  # the row as Alpaca returned it (JSON), so nothing is lost
])


# ---------------------------------------------------------------- what to ask for


@dataclass(frozen=True)
class Request:
    """One company to download: a ticker, or a ticker as of a date inside its life (a retired or reused ticker)."""

    symbol: str
    asof: date | None = None

    @property
    def key(self) -> str:
        return self.symbol if self.asof is None else f"{self.symbol}@{self.asof.isoformat()}"


def parse_key(key: str) -> Request:
    sym, _, asof = key.partition("@")
    return Request(parse_symbols(sym)[0], date.fromisoformat(asof) if asof else None)


def read_symbols_file(path: str | Path, start: date = START, today: date | None = None) -> list[Request]:
    """`symbol,asof_date` lines (header required) -> requests, order kept, exact duplicates dropped.

    Every line is checked before anything is sent: the symbol must be a plain ticker (it becomes a URL segment and
    a file name) and the date must be a real date between `start` and today. Raises ValueError naming the lines."""
    p = Path(path)
    with p.open(newline="") as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r) and not r[0].lstrip().startswith("#")]
    if not rows or [c.strip().lower() for c in rows[0]] != FILE_HEADER:
        raise ValueError(f"{p}: the first line must be the header '{','.join(FILE_HEADER)}'")
    out: dict[str, Request] = {}
    bad: list[str] = []
    for n, r in enumerate(rows[1:], 2):
        try:
            if len(r) != 2:
                raise ValueError("expected two columns")
            sym = parse_symbols(r[0])
            if len(sym) != 1:
                raise ValueError("one symbol per line")
            asof = date.fromisoformat(r[1].strip())
            if asof < start or (today is not None and asof > today):
                raise ValueError(f"asof_date must be between {start} and today")
        except ValueError as e:
            bad.append(f"line {n} ({','.join(r)!r}): {e}")
            continue
        req = Request(sym[0], asof)
        out.setdefault(req.key, req)
    if bad:
        raise ValueError(f"{p}: " + "; ".join(bad[:10]) + (f"; and {len(bad) - 10} more" if len(bad) > 10 else ""))
    return list(out.values())


def complete_through(now: datetime) -> date:
    """Last calendar day whose session (if it had one) is complete at `now`."""
    et = now.astimezone(ET)
    return et.date() if et.time() >= COMPLETE_AT else et.date() - timedelta(days=1)


def _weekday_between(after: date, upto: date) -> bool:
    """Is there a Monday-Friday date d with after < d <= upto (a session that may be missing)?"""
    d = after + timedelta(days=1)
    while d <= upto:
        if d.weekday() < 5:
            return True
        d += timedelta(days=1)
    return False


# ---------------------------------------------------------------- files


def bars_path(root: Path, key: str) -> Path:
    return Path(root) / "bars" / f"symbol={key}.parquet"


def ref_path(root: Path, key: str) -> Path:
    return Path(root) / "ref_all" / f"symbol={key}.parquet"


def actions_path(root: Path) -> Path:
    return Path(root) / "corporate_actions.parquet"


def holes_path(root: Path) -> Path:
    return Path(root) / "holes.csv"


def write_table(table: pa.Table, path: Path, meta: dict) -> None:
    """Atomic: a reader or an interrupted run never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table.replace_schema_metadata({META_KEY: json.dumps(meta, sort_keys=True).encode()}), tmp,
                   compression="zstd")
    os.replace(tmp, path)


def read_meta(path: Path) -> dict:
    """The small JSON written into the Parquet footer ({} when the file is missing or unreadable)."""
    try:
        raw = (pq.read_schema(path).metadata or {}).get(META_KEY)
        return json.loads(raw) if raw else {}
    except (OSError, ValueError, pa.ArrowException):
        return {}


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True))
    os.replace(tmp, path)


def read_hole(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def store_keys(root: Path) -> list[str]:
    return sorted(p.name[len("symbol="):-len(".parquet")] for p in (Path(root) / "bars").glob("symbol=*.parquet"))


# ---------------------------------------------------------------- bars


def session_date(t: str) -> date:
    """Alpaca stamps a daily bar with midnight in New York (04:00 or 05:00 UTC)."""
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET).date()


def fetch_daily(api, symbol: str, start: date, end: datetime, adjustment: str = "raw",
                asof: date | None = None) -> list[dict]:
    """All pages of daily bars for one symbol, oldest first, exactly as Alpaca returns them."""
    params = {"timeframe": "1Day", "start": datetime(start.year, start.month, start.day, tzinfo=UTC).isoformat(),
              "end": end.isoformat(), "limit": 10000, "feed": "sip", "adjustment": adjustment}
    if asof is not None:
        params["asof"] = asof.isoformat()
    rows: list[dict] = []
    token = None
    for _ in range(MAX_PAGES):
        d = api.get(f"/v2/stocks/{symbol}/bars", {**params, "page_token": token} if token else params)
        rows += d.get("bars") or []
        token = d.get("next_page_token")
        if not token:
            return rows
    raise ValueError(f"{symbol}: the daily bars did not end after {MAX_PAGES} pages")


def _clean(rows: Iterable[dict], through: date) -> list[tuple[date, dict]]:
    """One bar per complete session, sorted. A session still running (or its evening) is left for the next run."""
    by_day: dict[date, dict] = {}
    for b in rows:
        d = session_date(b["t"])
        if d <= through:
            by_day[d] = b
    return sorted(by_day.items())


def bars_table(rows: Iterable[dict], through: date) -> pa.Table:
    days = _clean(rows, through)
    ts = [datetime.combine(d, COMPLETE_AT, ET).astimezone(UTC) for d, _ in days]
    return pa.table({
        "date": pa.array([d for d, _ in days], pa.date32()),
        "o": pa.array([float(b["o"]) for _, b in days], pa.float64()),
        "h": pa.array([float(b["h"]) for _, b in days], pa.float64()),
        "l": pa.array([float(b["l"]) for _, b in days], pa.float64()),
        "c": pa.array([float(b["c"]) for _, b in days], pa.float64()),
        "v": pa.array([float(b["v"]) for _, b in days], pa.float64()),
        "n": pa.array([int(b.get("n", 0)) for _, b in days], pa.int64()),
        "vw": pa.array([float(b.get("vw", b["c"])) for _, b in days], pa.float64()),
        "available_ts": pa.array(ts, pa.timestamp("s", tz="UTC")),
    }, schema=BARS_SCHEMA)


def ref_table(rows: Iterable[dict], through: date) -> pa.Table:
    days = _clean(rows, through)
    return pa.table({"date": pa.array([d for d, _ in days], pa.date32()),
                     "c": pa.array([float(b["c"]) for _, b in days], pa.float64()),
                     "v": pa.array([float(b["v"]) for _, b in days], pa.float64())}, schema=REF_SCHEMA)


def _dead(last: date, through: date) -> bool:
    return last < through - timedelta(days=DEAD_AFTER_DAYS)


def is_fresh(meta: dict, req: Request, start: date, through: date) -> bool:
    """Does a stored file already hold everything a request made now would return? Raw bars never change after
    the fact, so a file is fresh until a new session may exist; an ended series (delisted) stays fresh for good."""
    if not meta or meta.get("schema") != SCHEMA_VERSION or meta.get("start") != start.isoformat():
        return False
    if meta.get("asof") != (req.asof.isoformat() if req.asof else None):
        return False
    try:
        got, last = date.fromisoformat(meta["through"]), date.fromisoformat(meta["last"])
    except (KeyError, TypeError, ValueError):
        return False
    return not _weekday_between(got, through) or (req.asof is not None and _dead(last, got))


@dataclass
class Outcome:
    key: str
    status: str  # fetched | fresh | kept | hole | hole_known
    rows: int = 0
    first: str = ""
    last: str = ""
    requests: int = 0
    reference: bool = False
    reason: str = ""


def _hole(root: Path, req: Request, reason: str, start: date, through: date, now: datetime, http: int = 0,
          first: str = "", last: str = "") -> None:
    p = bars_path(root, req.key)
    _write_json(p.with_suffix(".empty"), {
        "key": req.key, "symbol": req.symbol, "asof": req.asof.isoformat() if req.asof else None, "reason": reason,
        "http": http, "first_returned": first, "last_returned": last, "start": start.isoformat(),
        "through": through.isoformat(), "checked_at": now.astimezone(UTC).isoformat(timespec="seconds")})


def fetch_key(api, req: Request, root: Path, start: date, now: datetime, *, reference: bool = True,
              refresh: bool = False, retry_holes: bool = False) -> Outcome:
    """Download one key (raw bars, then Alpaca's adjusted series for the check) unless the store already has it.

    A ticker Alpaca does not know, a ticker without bars, and an as-of request whose bars do not cover its date
    (Alpaca fell back to another company with the same ticker) leave a `.empty` marker instead of a bar file.
    If the store already has bars for the key they are kept as they are (status `kept`).
    Any other HTTP failure is raised: the caller stops and the next run resumes here."""
    root = Path(root)
    through = complete_through(now)
    end = now - SIP_DELAY
    p, marker = bars_path(root, req.key), bars_path(root, req.key).with_suffix(".empty")
    if not refresh:
        if marker.exists() and not retry_holes:
            return Outcome(req.key, "hole_known", reason=read_hole(marker).get("reason", ""))
        meta = read_meta(p) if p.exists() else {}
        if is_fresh(meta, req, start, through) and (not reference or ref_path(root, req.key).exists()
                                                    or meta.get("reference") == "none"):
            return Outcome(req.key, "fresh", meta.get("rows", 0), meta.get("first", ""), meta.get("last", ""),
                           reference=ref_path(root, req.key).exists())
    def unusable(reason: str, http: int = 0, first: str = "", last: str = "") -> Outcome:
        if p.exists():  # bars stored earlier are never replaced by a hole: Alpaca's answer today may be a hiccup
            old = read_meta(p)
            return Outcome(req.key, "kept", old.get("rows", 0), old.get("first", ""), old.get("last", ""), 1,
                           ref_path(root, req.key).exists(), reason)
        _hole(root, req, reason, start, through, now, http=http, first=first, last=last)
        return Outcome(req.key, "hole", requests=1, first=first, last=last, reason=reason)

    try:
        rows = fetch_daily(api, req.symbol, start, end, "raw", req.asof)
    except HttpError as e:
        if e.status not in SKIP_STATUSES:
            raise
        return unusable(f"http_{e.status}", http=e.status)
    table = bars_table(rows, through)
    days = table.column("date").to_pylist()
    if not days:
        return unusable("no_bars")
    first, last = days[0].isoformat(), days[-1].isoformat()
    if req.asof is not None and not any(abs((d - req.asof).days) <= ASOF_WINDOW_DAYS for d in days):
        return unusable("asof_not_covered", first=first, last=last)
    n_req, ref_state = 1, "off"
    ref_rows = None
    if reference:
        n_req += 1
        try:
            ref_rows = ref_table(fetch_daily(api, req.symbol, start, end, "all", req.asof), through)
            ref_state = "ok" if ref_rows.num_rows else "none"
        except HttpError as e:
            if e.status not in SKIP_STATUSES:
                raise
            ref_state = "none"
    meta = {"schema": SCHEMA_VERSION, "key": req.key, "symbol": req.symbol,
            "asof": req.asof.isoformat() if req.asof else None, "start": start.isoformat(),
            "through": through.isoformat(), "first": first, "last": last, "rows": table.num_rows,
            "fetched_at": now.astimezone(UTC).isoformat(timespec="seconds"), "feed": "sip", "reference": ref_state}
    if ref_state == "ok":
        write_table(ref_rows, ref_path(root, req.key), {**meta, "adjustment": "all", "rows": ref_rows.num_rows})
    else:
        ref_path(root, req.key).unlink(missing_ok=True)
    write_table(table, p, {**meta, "adjustment": "raw"})  # last: a bar file means the key is complete
    marker.unlink(missing_ok=True)
    return Outcome(req.key, "fetched", table.num_rows, first, last, n_req, ref_state == "ok")


def write_holes(root: Path) -> int:
    """Rebuild holes.csv from the `.empty` markers. Returns the number of holes."""
    root = Path(root)
    rows = [read_hole(p) for p in sorted((root / "bars").glob("symbol=*.empty"))]
    cols = ["key", "symbol", "asof", "reason", "http", "first_returned", "last_returned", "checked_at"]
    path = holes_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in cols])
    os.replace(tmp, path)
    return len(rows)


# ---------------------------------------------------------------- corporate actions


def _day(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _num(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return math.nan
    return x if math.isfinite(x) else math.nan


def _flag(v) -> bool:
    return v is True or str(v).strip().lower() == "true"  # some Alpaca APIs send these as "true" / "false"


def normalise_action(kind: str, r: dict) -> list[dict]:
    """One Alpaca row -> store rows (one per symbol it concerns), [] when it has no usable symbol, date or rate.
    Every field is read with `.get`: Alpaca omits fields it does not have (a split may lack `payable_date`)."""
    ratio = cash = math.nan
    symbols = [r.get("symbol")]
    when = _day(r.get("ex_date")) or _day(r.get("process_date"))
    if kind in ("forward_split", "reverse_split"):
        new, old = _num(r.get("new_rate")), _num(r.get("old_rate"))
        ratio = new / old if new > 0 and old > 0 else math.nan
    elif kind == "stock_dividend":
        rate = _num(r.get("rate"))
        ratio = 1.0 + rate if rate > 0 else math.nan
    elif kind == "cash_dividend":
        cash = _num(r.get("rate"))
        cash = cash if cash > 0 else math.nan
    elif kind == "spin_off":
        symbols = [r.get("source_symbol") or r.get("symbol")]
    elif kind == "name_change":
        symbols = [r.get("old_symbol"), r.get("new_symbol")]
        when = _day(r.get("process_date")) or when
    if when is None or (kind in SHARE_TYPES and not ratio > 0) or (kind == "cash_dividend" and not cash > 0):
        return []
    base = {"type": kind, "ex_date": when, "ratio": ratio, "cash": cash, "special": _flag(r.get("special")),
            "foreign": _flag(r.get("foreign")), "process_date": _day(r.get("process_date")),
            "record_date": _day(r.get("record_date")), "payable_date": _day(r.get("payable_date")),
            "cusip": str(r.get("cusip") or r.get("new_cusip") or r.get("source_cusip") or ""),
            "id": str(r.get("id") or ""), "raw": json.dumps(r, sort_keys=True)}
    return [{**base, "symbol": str(s).upper()} for s in dict.fromkeys(symbols) if s]


def _ca_request(api, symbols: Sequence[str], start: date, end: date) -> tuple[list[dict], int, int]:
    rows: list[dict] = []
    pages = unusable = 0
    token = None
    while pages < MAX_PAGES:
        p = {"symbols": ",".join(symbols), "types": ",".join(CA_TYPES), "start": start.isoformat(),
             "end": end.isoformat(), "limit": CA_PAGE}
        if token:
            p["page_token"] = token
        d = api.get(CA_PATH, p)
        pages += 1
        block = d.get("corporate_actions") or {}
        if not isinstance(block, dict):
            raise ValueError("unexpected corporate-actions response: 'corporate_actions' is not an object of "
                             f"lists per type (got {type(block).__name__})")
        for name, items in block.items():
            kind = CA_ARRAYS.get(name)
            for r in items if kind and isinstance(items, list) else []:
                got = normalise_action(kind, r) if isinstance(r, dict) else []
                unusable += not got
                rows += got
        token = d.get("next_page_token")
        if not token:
            return rows, pages, unusable
    raise ValueError(f"the corporate actions did not end after {MAX_PAGES} pages")


def _years(start: date, end: date) -> list[tuple[date, date]]:
    return [(max(start, date(y, 1, 1)), min(end, date(y, 12, 31))) for y in range(start.year, end.year + 1)]


def fetch_actions(api, symbols: Sequence[str], start: date, today: date, chunk: int = CA_CHUNK,
                  log: Callable[[str], None] | None = None) -> tuple[list[dict], dict]:
    """Splits, stock dividends, cash dividends, spin-offs and name changes of `symbols`, from `start` to the
    actions already announced for the next months. Returns (rows not yet tied to a store key, request info).

    The documentation gives no maximum range and does not say whether a future `end` is allowed. So the first
    attempt is one range per chunk of symbols; if Alpaca answers 400 or 422 to that, the same symbols are asked
    again one calendar year at a time up to today, then once for the coming months (a refusal of that last
    request is noted, not fatal), and that mode is kept for the remaining chunks."""
    syms = sorted(dict.fromkeys(symbols))
    future = today + timedelta(days=CA_FUTURE_DAYS)
    rows: list[dict] = []
    info = {"requests": 0, "mode": "one_range", "future": "included", "unusable_rows": 0, "symbols": len(syms),
            "start": start.isoformat(), "end": future.isoformat()}

    def ask(part: Sequence[str], windows: Sequence[tuple[date, date]]) -> list[dict]:
        got: list[dict] = []
        for a, b in windows:
            info["requests"] += 1  # counted before the call, so a refused request is counted too
            r, pages, bad = _ca_request(api, part, a, b)
            got += r
            info["requests"] += pages - 1
            info["unusable_rows"] += bad
        return got

    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        got = None
        if info["mode"] == "one_range":
            try:
                got = ask(part, [(start, future)])
            except HttpError as e:
                if e.status not in (400, 422):
                    raise
                info["mode"] = "yearly_windows"
                if log:
                    log(f"corporate actions: HTTP {e.status} for the single range {start} -> {future}; "
                        "asking one calendar year at a time instead")
        if got is None:
            got = ask(part, _years(start, today))
            if info["future"] != "refused":
                try:
                    got += ask(part, [(today + timedelta(days=1), future)])
                except HttpError as e:
                    if e.status not in (400, 422):
                        raise
                    info.update(future="refused", end=today.isoformat())
        rows += got
        if log:
            log(f"corporate actions: {min(i + chunk, len(syms))}/{len(syms)} symbols, {len(rows)} rows")
    return rows, info


@dataclass
class Span:
    """What the store holds for one key: the plain ticker and the first and last session of its bars."""

    key: str
    symbol: str
    first: date
    last: date
    ended: bool  # the series stopped (delisted or acquired): later actions under this ticker are another company's


def store_spans(root: Path) -> list[Span]:
    out = []
    for key in store_keys(root):
        m = read_meta(bars_path(root, key))
        try:
            first, last, through = (date.fromisoformat(m[k]) for k in ("first", "last", "through"))
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Span(key, m.get("symbol") or key.partition("@")[0], first, last, _dead(last, through)))
    return out


def attribute_actions(rows: Iterable[dict], spans: Iterable[Span]) -> list[dict]:
    """Tie each action to the store keys whose bars it can affect, and flag repeats.

    A ticker can belong to different companies over time (DOW before 2017 and DOW since 2019), and Alpaca's rows
    carry only the ticker. So an action goes to a key when the ticker matches and its ex-date lies inside that
    key's bars: after the first bar (there is nothing earlier to adjust) and, for a series that has ended, not
    after its last bar. Spin-offs and name changes are information and are kept from the first bar on.

    Alpaca sometimes lists one payment twice (two ids, often two CUSIPs). Rows with the same key, type, ex-date,
    amount and special flag as an earlier row get `dup=True`; nothing is deleted."""
    by_symbol: dict[str, list[Span]] = {}
    for s in spans:
        by_symbol.setdefault(s.symbol, []).append(s)
    out: list[dict] = []
    for r in rows:
        for s in by_symbol.get(r["symbol"], []):
            e = r["ex_date"]
            lo_ok = e > s.first if r["type"] in ADJUSTING_TYPES else e >= s.first
            if lo_ok and (not s.ended or e <= s.last):
                out.append({**r, "key": s.key})
    out.sort(key=lambda r: (r["key"], r["ex_date"], r["type"], r["id"], r["cusip"], r["raw"]))
    seen: set[tuple] = set()
    exact: set[tuple] = set()
    kept: list[dict] = []
    for r in out:
        if (r["key"], r["raw"]) in exact:  # the very same row twice (overlapping pages or windows)
            continue
        exact.add((r["key"], r["raw"]))
        amount = r["cash"] if r["type"] == "cash_dividend" else r["ratio"]
        ident = (r["key"], r["type"], r["ex_date"], None if math.isnan(amount) else round(amount, 9), r["special"])
        r["dup"] = r["type"] in ADJUSTING_TYPES and ident in seen
        seen.add(ident)
        kept.append(r)
    return kept


def actions_table(rows: Sequence[dict]) -> pa.Table:
    return pa.table({f.name: pa.array([r[f.name] for r in rows], f.type) for f in ACTIONS_SCHEMA},
                    schema=ACTIONS_SCHEMA)


def sync_actions(api, root: Path, start: date, now: datetime, chunk: int = CA_CHUNK,
                 log: Callable[[str], None] | None = None) -> dict:
    """Fetch the corporate actions of every key in the store and replace the table (small: always all of it)."""
    root = Path(root)
    spans = store_spans(root)
    today = now.astimezone(ET).date()
    rows, info = fetch_actions(api, [s.symbol for s in spans], start, today, chunk, log)
    kept = attribute_actions(rows, spans)
    by_type: dict[str, int] = {}
    for r in kept:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    ex = [r["ex_date"] for r in kept if r["type"] in ADJUSTING_TYPES]
    info.update(rows=len(kept), by_type=by_type, duplicates=sum(r["dup"] for r in kept),
                first_ex_date=min(ex).isoformat() if ex else None, last_ex_date=max(ex).isoformat() if ex else None,
                fetched_at=now.astimezone(UTC).isoformat(timespec="seconds"), schema=SCHEMA_VERSION)
    write_table(actions_table(kept), actions_path(root), info)
    return info


# ---------------------------------------------------------------- status


@dataclass
class Status:
    keys: int = 0
    rows: int = 0
    first: str = ""
    last: str = ""
    references: int = 0
    holes: int = 0
    actions: dict = field(default_factory=dict)
    bytes: int = 0


def store_status(root: Path) -> Status:
    root = Path(root)
    st = Status()
    for key in store_keys(root):
        m = read_meta(bars_path(root, key))
        st.keys += 1
        st.rows += int(m.get("rows", 0))
        st.first = min(filter(None, (st.first, m.get("first", ""))), default="")
        st.last = max(st.last, m.get("last", ""))
    st.references = len(list((root / "ref_all").glob("symbol=*.parquet")))
    st.holes = len(list((root / "bars").glob("symbol=*.empty")))
    st.actions = read_meta(actions_path(root))
    st.bytes = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
    return st
