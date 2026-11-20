"""The market-event alarm (story S103, #377): one read-only set of flags per session, known before the open.

Pre-registered in docs/prereg/market_alarm_study.md (flags, inputs, `available_ts`, tiers). Design:
docs/research/event_driven/design.md, section 4. **It gates nothing**: no order path, risk check or strategy reads
it. The engine only writes it as a `market_alarm` event (behind `KAIBOT_MARKET_ALARM`), and
scripts/market_alarm_report.py backfills and measures it.

    alarm(session, as_of, inputs)  -> {"session", "as_of", "flags": {...}, "tier", "n_megacaps", "details", ...}

`alarm` is pure: it reads only the `Inputs` it is given and, of those, only entries with `available_ts <= as_of`
(tests/unit/test_market_alarm.py deletes every later entry and checks that nothing changes). The loaders below
turn the committed calendars and the research files into `Inputs`; they do the I/O, `alarm` does none.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[3]
MACRO_CALENDAR = ROOT / "config" / "macro_calendar.yaml"
MARKET_EVENTS = ROOT / "config" / "market_events.yaml"
TOP10_FILE = Path("alarm") / "top10.csv"  # under the data folder; written by scripts/market_alarm_report.py
ANNOUNCEMENTS = Path("earnings") / "announcements.parquet"

FLAGS = ("fed_day", "cpi_day", "megacap_report", "index_reshuffle", "mega_listing")
TESTED_FLAGS = ("fed_day", "cpi_day", "megacap_report", "index_reshuffle")
MEGACAP_MAJOR = 3  # n_megacaps >= this makes the session `major` (the pre-registered value before tightening)
SCHEDULE_LEAD = timedelta(days=7)  # scheduled dates count as known this long before the session (pre-registered)
AS_OF_ET = time(9, 0)  # the alarm of a session is computed as of 09:00 ET that day
TOP10_LAG = timedelta(minutes=15)  # a ranking made on a close is known 15 minutes after it (SIP delay)
TOP10_MAX_AGE = timedelta(days=45)  # live: an older ranking is stale and megacap_report is unknown


@dataclass(frozen=True)
class Dated:
    """One calendar entry: `kind` happens on (or takes effect at the close of) `session`; public from
    `available_ts`."""

    kind: str  # fomc | cpi | index:<list> | mega_listing | mega_listing:<follow-up kind>
    session: date
    available_ts: datetime
    detail: Mapping = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    """One earnings report (an 8-K Item 2.02 with `report = True`, S85)."""

    company: str
    symbol: str
    session: date
    available_ts: datetime  # SEC acceptance time


@dataclass(frozen=True)
class Ranking:
    """The ten largest companies for the sessions of `month` (the first day of a month)."""

    month: date
    available_ts: datetime
    companies: frozenset[str]
    symbols: Mapping[str, str] = field(default_factory=dict)  # company -> ticker on the ranking day


@dataclass(frozen=True)
class Inputs:
    events: tuple[Dated, ...] = ()
    reports: tuple[Report, ...] = ()
    rankings: tuple[Ranking, ...] = ()
    missing: tuple[str, ...] = ()  # inputs that could not be read: their flag is None (unknown), never False
    covers_until: Mapping[str, date] = field(default_factory=dict)


def as_of_for(session: date) -> datetime:
    """09:00 ET on the session, in UTC."""
    return datetime.combine(session, AS_OF_ET, tzinfo=ET).astimezone(UTC)


def _list_of(kind: str) -> str:
    return kind.split(":", 1)[1] if kind.startswith("index:") else ("mega_listing" if kind.startswith("mega") else "")


def alarm(session: date, as_of: datetime, inputs: Inputs, megacap_major: int = MEGACAP_MAJOR) -> dict:
    """The flags of `session` from the entries of `inputs` public at `as_of`. Pure; never raises on odd entries."""
    known = [e for e in inputs.events if e.available_ts <= as_of and e.session == session]
    kinds = {e.kind for e in known}
    flags: dict[str, bool | None] = {
        "fed_day": "fomc" in kinds,
        "cpi_day": "cpi" in kinds,
        "index_reshuffle": any(k.startswith("index:") for k in kinds),
        "mega_listing": any(k.startswith("mega_listing") for k in kinds),
    }
    stale = sorted(name for name, until in inputs.covers_until.items() if until is not None and session > until)
    for name in stale:  # a stale list cannot say "no": its flag is unknown
        if name == "mega_listing" and not flags["mega_listing"]:
            flags["mega_listing"] = None
        elif name != "mega_listing" and not flags["index_reshuffle"]:
            flags["index_reshuffle"] = None
    # the ten largest: the newest ranking public at as_of
    ranks = [r for r in inputs.rankings if r.available_ts <= as_of and r.month <= session]
    rank = max(ranks, key=lambda r: (r.month, r.available_ts)) if ranks else None
    reporters: list[str] = []
    if "reports" in inputs.missing or rank is None:
        flags["megacap_report"] = None
        n_mega = None
    else:
        reporters = sorted({r.company for r in inputs.reports
                            if r.session == session and r.available_ts <= as_of and r.company in rank.companies})
        n_mega = len(reporters)
        flags["megacap_report"] = n_mega > 0
    for name in inputs.missing:
        if name in ("macro_calendar",):
            flags["fed_day"] = flags["fed_day"] or None
            flags["cpi_day"] = flags["cpi_day"] or None
        if name in ("market_events",):
            flags["index_reshuffle"] = flags["index_reshuffle"] or None
            flags["mega_listing"] = flags["mega_listing"] or None
    flags = {k: flags[k] for k in FLAGS}
    major = bool(flags["fed_day"] or flags["mega_listing"] or (n_mega is not None and n_mega >= megacap_major))
    tier = "major" if major else ("notice" if any(flags.values()) else "none")
    details = {
        "index": sorted(_list_of(e.kind) for e in known if e.kind.startswith("index:")),
        "mega_listing": sorted(
            {f"{e.detail.get('symbol', '?')}{'' if e.kind == 'mega_listing' else ' ' + e.kind.split(':', 1)[1]}"
             for e in known if e.kind.startswith("mega_listing")}),
        "megacaps_reporting": [rank.symbols.get(c, c) for c in reporters] if rank is not None else [],
        "ranking_month": rank.month.isoformat() if rank is not None else None,
    }
    return {"session": session.isoformat(), "as_of": as_of.astimezone(UTC).isoformat(), "flags": flags,
            "tier": tier, "n_megacaps": n_mega, "megacap_major": megacap_major, "details": details,
            "unknown": sorted(k for k, v in flags.items() if v is None), "stale": stale,
            "missing": sorted(inputs.missing)}


# ---------------------------------------------------------------- loaders (I/O)
def _day(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _scheduled(kind: str, d: date, **detail) -> Dated:
    return Dated(kind, d, datetime.combine(d - SCHEDULE_LEAD, time(0), tzinfo=ET).astimezone(UTC), detail)


def calendar_events(macro: Path = MACRO_CALENDAR, market: Path = MARKET_EVENTS) -> tuple[list[Dated], dict, list]:
    """(events, covers_until per list, missing) from the two committed calendars."""
    out: list[Dated] = []
    covers: dict[str, date] = {}
    missing: list[str] = []
    try:
        mc = yaml.safe_load(Path(macro).read_text()) or {}
        out += [_scheduled("fomc", _day(d)) for d in mc.get("fomc_statement_1400") or []]
        out += [_scheduled("cpi", _day(d)) for d in mc.get("cpi_0830") or []]
    except (OSError, yaml.YAMLError, ValueError):
        missing.append("macro_calendar")
    try:
        ev = yaml.safe_load(Path(market).read_text()) or {}
        covers = {k: _day(v) for k, v in (ev.get("covers_until") or {}).items()}
        out += [_scheduled("cpi", _day(d)) for d in ev.get("cpi_0830_history") or []]
        for name, days in (ev.get("index_reshuffle") or {}).items():
            out += [_scheduled(f"index:{name}", _day(d)) for d in days or []]
        for m in ev.get("mega_listing") or []:
            known = datetime.combine(_day(m["known_since"]), time(0), tzinfo=ET).astimezone(UTC)
            out.append(Dated("mega_listing", _day(m["first_session"]), known, {"symbol": m.get("symbol")}))
            for f in m.get("follow_ups") or []:
                fk = datetime.combine(_day(f["known_since"]), time(0), tzinfo=ET).astimezone(UTC)
                out.append(Dated(f"mega_listing:{f['kind']}", _day(f["session"]), fk, {"symbol": m.get("symbol")}))
    except (OSError, yaml.YAMLError, ValueError, KeyError, TypeError):
        missing.append("market_events")
    return out, covers, missing


def reports_from_frame(df) -> list[Report]:
    """S85's announcements table -> reports (`report = True` rows only)."""
    import pandas as pd

    t = df[df["report"].astype(bool)] if "report" in df.columns else df
    acc = pd.to_datetime(t["accepted_ts"], utc=True)
    return [Report(str(c), str(s), _day(ses), a.to_pydatetime())
            for c, s, ses, a in zip(t["company"], t["symbol"], t["session"], acc, strict=True)
            if ses is not None and str(ses) not in ("NaT", "nan", "None")]


def read_rankings(path: Path) -> list[Ranking]:
    """data/alarm/top10.csv (month, available_ts, rank, company, symbol) -> rankings."""
    import pandas as pd

    t = pd.read_csv(path)
    out = []
    for (month, avail), g in t.groupby(["month", "available_ts"], sort=True):
        out.append(Ranking(_day(month), pd.Timestamp(avail).tz_convert(UTC).to_pydatetime()
                           if pd.Timestamp(avail).tzinfo else pd.Timestamp(avail).tz_localize(UTC).to_pydatetime(),
                           frozenset(g["company"].astype(str)), dict(zip(g["company"].astype(str),
                                                                         g["symbol"].astype(str), strict=True))))
    return out


def load_inputs(data: Path | None = None, macro: Path = MACRO_CALENDAR, market: Path = MARKET_EVENTS) -> Inputs:
    """Everything the alarm reads, from the committed calendars and the data folder (live and backfill alike).

    A file that is missing makes its flag unknown (None), never False."""
    import pandas as pd

    data = Path(data) if data is not None else ROOT / "data"
    events, covers, missing = calendar_events(macro, market)
    reports: list[Report] = []
    try:
        reports = reports_from_frame(pd.read_parquet(data / ANNOUNCEMENTS,
                                                     columns=["company", "symbol", "session", "accepted_ts", "report"]))
    except Exception:  # noqa: BLE001 - any unreadable file: the flag is unknown
        missing.append("reports")
    rankings: list[Ranking] = []
    try:
        rankings = read_rankings(data / TOP10_FILE)
    except Exception:  # noqa: BLE001
        missing.append("rankings")
    return Inputs(tuple(events), tuple(reports), tuple(rankings), tuple(missing), covers)


def live_alarm(session: date, now: datetime, data: Path | None = None) -> dict:
    """The alarm the engine logs at session start: `load_inputs` + `alarm`, as of `now` or 09:00 ET, whichever is
    earlier, so a restart during the session logs what the replay computes. A ranking older than TOP10_MAX_AGE is
    not used (megacap_report unknown)."""
    as_of = min(now.astimezone(UTC), as_of_for(session))
    inp = load_inputs(data)
    fresh = tuple(r for r in inp.rankings if as_of - r.available_ts <= TOP10_MAX_AGE)
    if len(fresh) != len(inp.rankings):
        missing = inp.missing if fresh or not inp.rankings else (*inp.missing, "rankings_stale")
        inp = Inputs(inp.events, inp.reports, fresh, missing, inp.covers_until)
    return alarm(session, as_of, inp)


def backfill(sessions: Iterable[date], inputs: Inputs, megacap_major: int = MEGACAP_MAJOR) -> list[dict]:
    """The alarm of every session, each as of 09:00 ET that day (the pre-registered time)."""
    return [alarm(s, as_of_for(s), inputs, megacap_major) for s in sessions]
