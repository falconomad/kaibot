"""The 15:50 minutes of the whole point-in-time universe (issue #794, decision 19 option B, follows #707).

Research data only: the live engine never imports this module. The 15:50 reader in `kaibot.replay` reads what it
writes.

What: for every key of the point-in-time research universe (the S&P 500 as it was, `daily_report.load_store`'s
"pit" view: today's 288 plain tickers, the SYMBOL@ASOF keys of every other company, the sector ETFs and SPY), the
1-minute SIP bars from 15:45 to 15:59 ET of every full session it needs, 2016 to the end of the research window
(config/holdout.yaml: nothing from the hold-out is asked for). A key needs a session when its company was a member
that day, or in the `TAIL_SESSIONS` sessions after it left (a position bought while it was a member is sold later).
Early-close sessions (13:00 ET) are never asked: they have no 15:50 minute, and a 15:45-15:59 bar that day is an
after-hours trade.

Which entity: each key is asked exactly as the daily store asked it (`daily_bars.parse_key`): its symbol with
`asof=<ASOF>` (Alpaca's symbol mapping as of that date, so renames are followed and a reused ticker means the
company of the key), a plain key with Alpaca's default (today's) mapping. Prices are `adjustment=split` and
`feed=sip`, the basis of the existing minute history (`data/bars_1m`), so `DailyStore.panel(intraday=...)` puts
them back on the raw basis and its 5% split-basis check drops a session of the wrong company.

How (requests): the bars endpoint's `limit` counts bars of all symbols together, so the cheapest plan per group of
keys with the same `asof` is chosen by estimate:
  - "batch": one request per session for the whole group, 15:45-15:59 ET only (15 bars a symbol, so up to 300
    symbols fit one page). The plain keys and the current members (all asked as of the same date) go this way.
  - "range": one key, one month, from its first to its last needed session; Alpaca returns every minute in
    between (extended hours included), about `PAGES_PER_MONTH` pages, and only 15:45-15:59 ET is kept. Former
    members, each with its own as-of date, go this way.

Layout: <data>/bars_1550/symbol=<KEY>/<YYYY-MM>.parquet (the schema of data/bars_1m; minutes 15:45-15:59 ET of
full sessions only), or <YYYY-MM>.empty (JSON: why nothing came back; "unknown_symbol" = Alpaca has no minute
history for the key, a coverage flag, never a guess). A month the full history (data/bars_1m) already holds is
never asked. Resumable: a written month is never asked again; an interrupted month is asked again in full.
Every request runs under the shared `alpaca` budget of config/sources.yaml (source `alpaca_minute_pit`).
"""

from __future__ import annotations

import bisect
import contextlib
import json
import os
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from kaibot.common.http import HttpError
from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.alpaca_history import ET

STORE = "bars_1550"
FULL = "bars_1m"
START = date(2016, 1, 4)  # the first session of the daily store
TAIL_SESSIONS = 21  # sessions after a spell ends that a held former member may still be sold in
WINDOW = (15 * 60 + 45, 16 * 60)  # minutes of the ET day kept: 15:45 <= bar start < 16:00
M_1550 = 15 * 60 + 50
BATCH_SYMBOLS = 300  # 300 x 15 bars = 4,500, one page of 10,000
PAGES_PER_MONTH = 2  # estimate for a "range" unit (a month of SIP minutes, extended hours included)
MAX_PAGES = 400  # a broken page token, not a real answer
MAX_ERRORS_IN_A_ROW = 5
SKIP_STATUSES = (400, 404, 422)  # Alpaca's answers for a symbol it does not know
BARS_PATH = "/v2/stocks/bars"
SCHEMA = pa.schema([
    ("ts", pa.timestamp("s", tz="UTC")), ("available_ts", pa.timestamp("s", tz="UTC")),
    ("o", pa.float64()), ("h", pa.float64()), ("l", pa.float64()), ("c", pa.float64()), ("v", pa.float64()),
    ("n", pa.int64()), ("vw", pa.float64()),
])


# ---------------------------------------------------------------- which keys, which sessions


@dataclass
class Target:
    """One key of the point-in-time universe and the sessions it needs a 15:50 minute for."""

    key: str
    symbol: str
    asof: date | None
    company: str | None  # None: an ETF or SPY (never a member, always in the view)
    seen_by_runs: bool  # the key `Membership.key_map` gives the company today (what pit runs read)
    member_days: list[date] = field(default_factory=list)  # sessions it was a member (the coverage denominator)
    need: list[date] = field(default_factory=list)  # member_days plus the tail after each spell


def split_key(key: str) -> tuple[str, date | None]:
    sym, _, asof = key.partition("@")
    return sym, date.fromisoformat(asof) if asof else None


def full_sessions(start: date, end: date) -> list[date]:
    return [d for d in nyse.sessions(start, end) if not nyse.early_close(d)]


def targets(store_keys: Iterable[str], membership, cfg: dict, start: date, end: date) -> list[Target]:
    """The point-in-time view's keys (`daily_report.load_store`, mode "pit") with the sessions each needs.

    A company's key is the first of `membership.keys(company)` the store holds, as `key_map` picks it. A current
    member without that key (a store built on another day than `sp500_pit.STORE_DAY`) is matched to the store's
    newest key of its symbol; such keys have `seen_by_runs` False (pit runs stop with KeyMismatchError, #812)."""
    have = set(store_keys)
    plain = [*cfg["sectors"], cfg["market"]]
    days = full_sessions(start, end)
    out: list[Target] = []
    for company, spells in membership.spells.items():
        exact = next((k for k in membership.keys(company) if k in have), None)
        key = exact
        if key is None and spells[-1].end is None:
            sym = membership.request(company)[0]
            alts = sorted(k for k in have if k.startswith(f"{sym}@"))
            key = alts[-1] if alts else None
        if key is None:
            continue
        member, need = set(), set()
        for sp in spells:
            lo = bisect.bisect_left(days, sp.start)
            hi = len(days) if sp.end is None else bisect.bisect_left(days, sp.end)
            member.update(days[lo:hi])
            need.update(days[lo:min(hi + (0 if sp.end is None else TAIL_SESSIONS), len(days))])
        if not need:
            continue
        sym, asof = split_key(key)
        out.append(Target(key, sym, asof, company, exact is not None, sorted(member), sorted(need)))
    seen = {t.key for t in out}
    for k in plain:
        if k in have and k not in seen:
            out.append(Target(k, k, None, None, True, [], list(days)))
    return sorted(out, key=lambda t: t.key)


# ---------------------------------------------------------------- what is still missing


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _held(folder: Path, month: str) -> bool:
    return (folder / f"{month}.parquet").exists() or (folder / f"{month}.empty").exists()


def missing(data: Path, t: Target) -> dict[str, list[date]]:
    """month -> needed sessions of `t` that neither the full history nor this store holds yet."""
    full, mine = data / FULL / f"symbol={t.key}", data / STORE / f"symbol={t.key}"
    out: dict[str, list[date]] = {}
    for d in t.need:
        m = month_of(d)
        if m in out or not (_held(full, m) or _held(mine, m)):
            out.setdefault(m, []).append(d)
    return out


@dataclass(frozen=True)
class Unit:
    """One resumable piece of work: its files are written together when it succeeds."""

    kind: str  # "batch" | "range"
    month: str
    asof: date | None
    days: dict[str, tuple[date, ...]]  # key -> its needed sessions in the month
    symbols: dict[str, str]  # key -> symbol asked

    @property
    def est_requests(self) -> int:
        if self.kind == "batch":
            return len({d for ds in self.days.values() for d in ds})
        return PAGES_PER_MONTH


def plan(data: Path, tgts: Sequence[Target]) -> list[Unit]:
    """The cheapest plan for every group of keys with the same as-of date, month by month (oldest first)."""
    groups: dict[date | None, list[tuple[Target, dict[str, list[date]]]]] = {}
    for t in tgts:
        todo = missing(data, t)
        if todo:
            groups.setdefault(t.asof, []).append((t, todo))
    units: list[Unit] = []
    for asof, members in groups.items():
        months = sorted({m for _, todo in members for m in todo})
        batch_cost = sum(len({d for _, todo in members for d in todo.get(m, ())}) for m in months)
        range_cost = sum(len(todo) for _, todo in members) * PAGES_PER_MONTH
        if batch_cost < range_cost:
            for m in months:
                ks = {t.key: tuple(todo[m]) for t, todo in members if m in todo}
                units.append(Unit("batch", m, asof, ks, {t.key: t.symbol for t, _ in members if t.key in ks}))
        else:
            for t, todo in members:
                for m, ds in todo.items():
                    units.append(Unit("range", m, asof, {t.key: tuple(ds)}, {t.key: t.symbol}))
    return sorted(units, key=lambda u: (u.month, u.kind, str(u.asof), min(u.days)))


# ---------------------------------------------------------------- asking Alpaca


def _et(d: date, minute: int) -> str:
    return datetime(d.year, d.month, d.day, minute // 60, minute % 60, tzinfo=ET).astimezone(UTC).isoformat()


def _keep(t: datetime) -> bool:
    et = t.astimezone(ET)
    m = et.hour * 60 + et.minute
    d = et.date()
    return WINDOW[0] <= m < WINDOW[1] and nyse.is_session(d) and not nyse.early_close(d)


class Asker:
    """GETs of the bars endpoint (an `alpaca_history.Alpaca`-like object with `get(path, params)`), counted."""

    def __init__(self, api) -> None:
        self.api = api
        self.requests = 0

    def bars(self, symbols: Sequence[str], start: str, end: str, asof: date | None) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        token = None
        for _ in range(MAX_PAGES):
            p = {"symbols": ",".join(symbols), "timeframe": "1Min", "start": start, "end": end, "limit": 10000,
                 "feed": "sip", "adjustment": "split", "sort": "asc"}
            if asof is not None:
                p["asof"] = asof.isoformat()
            if token:
                p["page_token"] = token
            self.requests += 1
            doc = self.api.get(BARS_PATH, p)
            got = doc.get("bars") if isinstance(doc, dict) else None
            for s, rows in (got or {}).items():
                if isinstance(rows, list):
                    out.setdefault(s, []).extend(r for r in rows if isinstance(r, dict))
            token = doc.get("next_page_token") if isinstance(doc, dict) else None
            if not token:
                return out
        raise HttpError(0, BARS_PATH, f"more than {MAX_PAGES} pages: a broken page token")

    def bars_split(self, symbols: Sequence[str], start: str, end: str, asof: date | None,
                   unknown: set[str]) -> dict[str, list[dict]]:
        """`bars`, halving the list on Alpaca's 4xx answer until the symbol it refuses is alone (`unknown`)."""
        try:
            return self.bars(symbols, start, end, asof)
        except HttpError as e:
            if e.status not in SKIP_STATUSES:
                raise
            if len(symbols) == 1:
                unknown.add(symbols[0])
                return {}
            h = len(symbols) // 2
            a = self.bars_split(symbols[:h], start, end, asof, unknown)
            b = self.bars_split(symbols[h:], start, end, asof, unknown)
            return {**a, **b}


def _table(rows: list[dict]) -> pa.Table:
    seen: dict[datetime, dict] = {}
    for b in rows:
        try:
            t = datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if _keep(t):
            seen[t] = b
    ts = sorted(seen)
    rs = [seen[t] for t in ts]
    return pa.table({
        "ts": pa.array(ts, pa.timestamp("s", tz="UTC")),
        "available_ts": pa.array([t + timedelta(minutes=1) for t in ts], pa.timestamp("s", tz="UTC")),
        "o": pa.array([float(b["o"]) for b in rs], pa.float64()), "h": pa.array([float(b["h"]) for b in rs]),
        "l": pa.array([float(b["l"]) for b in rs]), "c": pa.array([float(b["c"]) for b in rs]),
        "v": pa.array([float(b["v"]) for b in rs]), "n": pa.array([int(b.get("n", 0)) for b in rs], pa.int64()),
        "vw": pa.array([float(b.get("vw", b["c"])) for b in rs]),
    }, schema=SCHEMA)


def _write(data: Path, key: str, month: str, rows: list[dict], reason: str, now: datetime) -> int:
    folder = data / STORE / f"symbol={key}"
    folder.mkdir(parents=True, exist_ok=True)
    table = _table(rows)
    if table.num_rows:
        tmp = folder / f".{month}.parquet.tmp"
        pq.write_table(table, tmp)
        os.replace(tmp, folder / f"{month}.parquet")
    else:
        (folder / f"{month}.empty").write_text(json.dumps({"reason": reason, "asked_at": now.isoformat(
            timespec="seconds")}) + "\n")
    return table.num_rows


def run_unit(asker: Asker, data: Path, u: Unit, now: datetime) -> dict[str, int]:
    """Ask Alpaca for one unit and write its files. key -> rows written. Raises HttpError (nothing written)."""
    unknown: set[str] = set()
    got: dict[str, list[dict]] = {k: [] for k in u.days}
    by_sym = {s: k for k, s in u.symbols.items()}
    if u.kind == "batch":
        for d in sorted({d for ds in u.days.values() for d in ds}):
            syms = [u.symbols[k] for k, ds in u.days.items() if d in ds]
            for i in range(0, len(syms), BATCH_SYMBOLS):
                ans = asker.bars_split(syms[i:i + BATCH_SYMBOLS], _et(d, WINDOW[0]), _et(d, WINDOW[1] - 1), u.asof,
                                       unknown)
                for s, rows in ans.items():
                    if s in by_sym:
                        got[by_sym[s]].extend(rows)
    else:
        (key, ds), = u.days.items()
        ans = asker.bars_split([u.symbols[key]], _et(ds[0], WINDOW[0]), _et(ds[-1], WINDOW[1] - 1), u.asof, unknown)
        got[key].extend(ans.get(u.symbols[key], []))
    return {k: _write(data, k, u.month, rows, "unknown_symbol" if u.symbols[k] in unknown else "no_bars", now)
            for k, rows in got.items()}


def fetch(api, data: Path, tgts: Sequence[Target], *, now: datetime | None = None, max_minutes: float = 0.0,
          limit: int = 0, log: Callable[[str], None] = print, clock: Callable[[], float] = time.monotonic) -> dict:
    """Run the plan, unit by unit, until it is done, `limit` units ran or `max_minutes` passed (then it stops
    between units; the next run goes on). Five failures in a row stop it too."""
    now = now or datetime.now(UTC)
    units = plan(data, tgts)
    total = len(units)
    if limit:
        units = units[:limit]
    est = sum(u.est_requests for u in plan(data, tgts))
    log(f"{total} units to fetch (about {est} requests); this run takes {len(units)}")
    asker, t0, done, errors, in_a_row, stopped = Asker(api), clock(), 0, {}, 0, ""
    for n, u in enumerate(units, 1):
        if max_minutes and clock() - t0 >= max_minutes * 60:
            stopped = f"time budget of {max_minutes:g} minutes used"
            break
        try:
            rows = run_unit(asker, data, u, now)
        except HttpError as e:
            errors[f"{u.kind} {u.month} {','.join(sorted(u.days))[:80]}"] = str(e)[:200]
            in_a_row += 1
            if in_a_row >= MAX_ERRORS_IN_A_ROW:
                stopped = f"{in_a_row} failures in a row"
                break
            continue
        in_a_row, done = 0, done + 1
        if n % 25 == 0 or n == len(units):
            log(f"  [{n}/{len(units)}] {u.month} {u.kind}: {len(rows)} keys, {sum(rows.values())} bars; "
                f"{asker.requests} requests, {clock() - t0:.0f} s")
    left = len(plan(data, tgts))
    if stopped:
        log(f"Stopped: {stopped}. {left} units left; the next run goes on from here.")
    return {"units_done": done, "units_left": left, "requests": asker.requests, "errors": errors,
            "stopped": stopped}


# ---------------------------------------------------------------- coverage


def _dates_1550(path: Path, cache: dict) -> set[str]:
    """Sessions (ISO) of one month file with a bar from 15:50 to 15:59 ET. Cached by size and mtime."""
    st = path.stat()
    sig = f"{st.st_size}:{st.st_mtime_ns}"
    hit = cache.get(str(path))
    if hit and hit[0] == sig:
        return set(hit[1])
    ts = pq.read_table(path, columns=["ts"]).column("ts").to_pandas()
    et = pd.to_datetime(ts, utc=True).dt.tz_convert(ET)
    m = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    days = sorted(set(et[(m >= M_1550) & (m < WINDOW[1])].dt.strftime("%Y-%m-%d")))
    cache[str(path)] = [sig, days]
    return set(days)


def coverage(data: Path, tgts: Sequence[Target], start: date, end: date) -> dict:
    """Per year: members, members with a 15:50 minute on at least one member session, member sessions and the
    share with a 15:50 minute; and the keys Alpaca has no minute history for (flagged, never filled)."""
    cache_path = data / STORE / "coverage_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    years = {y: {"year": y, "members": 0, "members_with_1550": 0, "member_sessions": 0, "sessions_with_1550": 0}
             for y in range(start.year, end.year + 1)}
    keys_with, flagged, partial, per_key = 0, [], [], {}
    for t in tgts:
        have: set[str] = set()
        months = sorted({month_of(d) for d in (t.member_days or t.need)})
        unknown = 0
        for m in months:
            for folder in (data / FULL / f"symbol={t.key}", data / STORE / f"symbol={t.key}"):
                p = folder / f"{m}.parquet"
                if p.exists():
                    have |= _dates_1550(p, cache)
                    break
                e = folder / f"{m}.empty"
                if e.exists():
                    with contextlib.suppress(ValueError):
                        unknown += json.loads(e.read_text() or "{}").get("reason") == "unknown_symbol"
                    break
        days = t.member_days or t.need
        hit = [d for d in days if d.isoformat() in have]
        keys_with += bool(hit)
        per_key[t.key] = {"sessions": len(days), "with_1550": len(hit), "seen_by_runs": t.seen_by_runs}
        if not hit:
            flagged.append({"key": t.key, "company": t.company, "unknown_symbol_months": unknown,
                            "months": len(months), "seen_by_runs": t.seen_by_runs})
        elif len(hit) < 0.9 * len(days):
            partial.append({"key": t.key, "share": round(len(hit) / len(days), 3)})
        for y in {d.year for d in t.member_days}:
            ds = [d for d in t.member_days if d.year == y]
            n = sum(d.isoformat() in have for d in ds)
            row = years[y]
            row["members"] += 1
            row["members_with_1550"] += n > 0
            row["member_sessions"] += len(ds)
            row["sessions_with_1550"] += n
    for row in years.values():
        row["share"] = round(row["sessions_with_1550"] / row["member_sessions"], 4) if row["member_sessions"] else None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache))
    seen = [t for t in tgts if t.seen_by_runs]
    return {"window": [start.isoformat(), end.isoformat()], "keys": len(tgts), "keys_with_1550": keys_with,
            "keys_seen_by_runs": len(seen),
            "keys_seen_by_runs_with_1550": sum(per_key[t.key]["with_1550"] > 0 for t in seen),
            "years": list(years.values()), "flagged_no_minutes": flagged, "partial": partial,
            "early_closes_excluded": sum(nyse.early_close(d) for d in nyse.sessions(start, end))}


def render(cov: dict) -> str:
    lines = [f"# Minute coverage of the point-in-time universe, {cov['window'][0]} to {cov['window'][1]} (#794)", "",
             f"- Keys with a 15:50 minute: **{cov['keys_with_1550']} of {cov['keys']}** (the whole universe: every "
             "company's key, the sector ETFs and SPY).",
             f"- Of the {cov['keys_seen_by_runs']} keys today's pit runs read (`Membership.key_map`, #812): "
             f"{cov['keys_seen_by_runs_with_1550']} with a 15:50 minute.",
             f"- Early-close sessions ({cov['early_closes_excluded']}) are not counted: they have no 15:50 minute.", "",
             "| Year | Members | with a 15:50 minute | Member sessions | with a 15:50 minute | Share |",
             "|---|---|---|---|---|---|"]
    for r in cov["years"]:
        lines.append(f"| {r['year']} | {r['members']} | {r['members_with_1550']} | {r['member_sessions']} | "
                     f"{r['sessions_with_1550']} | {r['share']} |")
    fl = cov["flagged_no_minutes"]
    lines += ["", f"Flagged, no minute history (filled at the closing print, counted; never guessed): {len(fl)}"]
    if fl:
        lines.append(", ".join(f"{f['key']}" + (" (Alpaca: unknown)" if f["unknown_symbol_months"] else "")
                               for f in fl))
    if cov["partial"]:
        lines += ["", f"Under 90% of member sessions: {len(cov['partial'])}: "
                  + ", ".join(f"{p['key']} {p['share']}" for p in cov["partial"])]
    return "\n".join(lines) + "\n"

