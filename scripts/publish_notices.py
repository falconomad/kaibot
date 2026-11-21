#!/usr/bin/env python3
"""The dashboard's market notices (#378, M8 S104): "New on the market" and "Big day", written for the dashboard.

A small job on the Mac, beside the engine and never inside it. It reads
- the new-listing watch's local store (S102: data/alt/listings, data/listings/ipos.parquet; `Listings`, live
  reading), for listings whose first session falls between four sessions back and five sessions ahead, and
- the market-event alarm (S103, `kaibot.features.market_alarm.live_alarm`, once PR #403 is merged; skipped while the
  module is not there) for today and the next five sessions, as known now,
and upserts one row per notice into kaibot.market_notice (migration 0035) with the service key. A row it wrote
before that it no longer finds (a listing reclassified, a date moved) is marked `withdrawn`. The dashboard reads the
rows through kb_market_notices(), which also adds the engine's own `market_alarm` events.

What it cannot do: it sends no order, reads no account, writes no trading table and calls nothing in the engine's
decision path. The engine never imports it (tests/unit/test_publish_notices.py). Its only remote calls are
PostgREST reads and writes of kaibot.market_notice. No price is published: a notice says what happens and when.

    python scripts/publish_notices.py                    # dry run: prints the notices it would write
    python scripts/publish_notices.py --write --env ~/Projects/kaibot-secrets/.env
    python scripts/publish_notices.py --now 2026-11-19T06:45:00Z   # as the 07:45 Berlin run of 19 Nov saw it

Off by default: nothing runs it unless KAIBOT_NOTICES=1 (then the nightly daily job of scripts/service_run.sh runs
it with --write after the bar refresh; a failure is logged and never fails the daily job).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.ingest import nyse_sessions as nyse  # noqa: E402

ET = ZoneInfo("America/New_York")
TABLE = "market_notice"
BACK, AHEAD = 4, 6  # the window: four sessions before today, today (if a session) and the next ones, six in all
LARGE_OFFER_USD = 10e9  # a listing that raised this much at its offer price is "major" even if the alarm misses it
LISTING_STATUSES = ("expected", "pre_listing", "listed")
ALARM_FLAGS = ("fed_day", "cpi_day", "megacap_report", "index_reshuffle", "mega_listing")
TITLES = {"fed_day": "The Fed announces its interest-rate decision",
          "cpi_day": "The US inflation report comes out",
          "megacap_report": "Some of the largest companies report their results",
          "index_reshuffle": "Index funds rebalance at the close",
          "mega_listing": "A very large company starts trading"}
_SUFFIX = re.compile(r"[,\s]+(inc\.?|incorporated|corp\.?|corporation|co\.?|company|pbc|plc|ltd\.?|limited|llc|"
                     r"l\.p\.|lp|n\.v\.|s\.a\.|ag|se|holdings?(?= inc)|class [a-c])$", re.I)


def window(today: date) -> tuple[list[date], list[date]]:
    """(the four sessions before today, oldest first; today if a session and the next ones, six in all)."""
    back = nyse.sessions(today - timedelta(days=21), today - timedelta(days=1))[-BACK:]
    ahead = nyse.sessions(today, today + timedelta(days=21))[:AHEAD]
    return back, ahead


def short_name(company: str | None, symbol: str) -> str:
    """'Anthropic, PBC' -> 'Anthropic'. The company's own name, without its legal form."""
    name = (company or "").strip()
    for _ in range(3):
        name = _SUFFIX.sub("", name).strip().rstrip(",")
    return name or symbol


def _day(v) -> date | None:
    if v is None or str(v) in ("", "NaT", "nan", "None"):
        return None
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def listing_rows(listings: Iterable[dict], back: list[date], ahead: list[date], now: datetime,
                 mega: Callable[[date], set[str]] = lambda d: set()) -> list[dict]:
    """One `new_listing` row per new listing (an IPO or a direct listing) whose first session is in the window."""
    from kaibot.ingest import listings_pit as lp

    lo, hi = (back or ahead)[0], ahead[-1]
    out = []
    for r in listings:
        sym, first = str(r.get("symbol") or ""), _day(r.get("first_session"))
        if (not r.get("is_new_listing") or r.get("kind") not in lp.NEW_LISTING_KINDS
                or r.get("status") not in LISTING_STATUSES or first is None or not lo <= first <= hi
                or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", sym)):
            continue
        rules = lp.first_eligible(first)
        paper = next(x for x in rules if x["rule"] == "trend_momentum")
        offer = (_num(r.get("offer_price")) or 0) * (_num(r.get("shares_offered")) or 0)
        major = sym in mega(first) or offer >= LARGE_OFFER_USD
        name = short_name(r.get("company"), sym)
        out.append({
            "id": f"new_listing:{sym}:{first.isoformat()}", "kind": "new_listing",
            "flag": "direct_listing" if r.get("kind") == "direct_listing" else "ipo",
            "tier": "major" if major else "notice", "session": first.isoformat(), "symbol": sym,
            "title": f"{name} ({sym}) starts trading"[:200], "starts": None,
            "first_eligible": paper["first_eligible"].isoformat(),
            "detail": {"name": name, "company": r.get("company"), "status": r.get("status"),
                       "exchange": r.get("exchange"), "date_from": r.get("first_session_source"),
                       "rules": [{"rule": x["rule"], "state": x["state"],
                                  "first_eligible": x["first_eligible"].isoformat(),
                                  "needs_sp500": x["index_earliest"] is not None} for x in rules]},
            "available_ts": now.isoformat(), "withdrawn": False,
        })
    return out


def _starts(flag: str, d: date) -> str | None:
    if flag == "fed_day":
        return datetime.combine(d, time(14, 0), ET).astimezone(UTC).isoformat()
    if flag == "cpi_day":
        return datetime.combine(d, time(8, 30), ET).astimezone(UTC).isoformat()
    if flag == "index_reshuffle":
        return nyse.close_utc(d).isoformat()
    return None


def alarm_rows(alarm: Callable[[date, datetime], dict] | None, ahead: list[date], now: datetime) -> list[dict]:
    """One `big_day` row per flag that is true, for today and the next sessions, as the alarm knows them now. A flag
    that is unknown (None) gives no row: a notice is never guessed."""
    if alarm is None:
        return []
    out = []
    for d in ahead:
        a = alarm(d, now)
        flags, det = a.get("flags") or {}, a.get("details") or {}
        for flag in ALARM_FLAGS:
            if flags.get(flag) is not True:
                continue
            n_mega = a.get("n_megacaps")
            major = flag in ("fed_day", "mega_listing") or (
                flag == "megacap_report" and isinstance(n_mega, int) and n_mega >= int(a.get("megacap_major") or 3))
            sym = None
            detail: dict = {}
            if flag == "mega_listing" and det.get("mega_listing"):
                sym = str(det["mega_listing"][0]).split(" ")[0] or None
                detail = {"what": list(det["mega_listing"])}
            elif flag == "index_reshuffle":
                detail = {"index": list(det.get("index") or [])}
            elif flag == "megacap_report":
                detail = {"megacaps": list(det.get("megacaps_reporting") or [])}
            out.append({"id": f"big_day:{flag}:{d.isoformat()}" + (f":{sym}" if sym else ""), "kind": "big_day",
                        "flag": flag, "tier": "major" if major else "notice", "session": d.isoformat(), "symbol": sym,
                        "title": TITLES[flag], "starts": _starts(flag, d), "first_eligible": None, "detail": detail,
                        "available_ts": now.isoformat(), "withdrawn": False})
    return out


def mega_from(alarm: Callable[[date, datetime], dict] | None, now: datetime) -> Callable[[date], set[str]]:
    """The symbols the alarm calls a very large listing on a session (its `mega_listing` details)."""
    def get(d: date) -> set[str]:
        if alarm is None:
            return set()
        try:
            a = alarm(d, now)
        except Exception:  # noqa: BLE001 - a notice's tier must not stop the job
            return set()
        if (a.get("flags") or {}).get("mega_listing") is not True:
            return set()
        return {str(x).split(" ")[0] for x in (a.get("details") or {}).get("mega_listing") or [] if " " not in str(x)}
    return get


def load_alarm() -> Callable[[date, datetime], dict] | None:
    """The S103 alarm when its module is there (PR #403), else None: then only listings are published."""
    try:
        from kaibot.features.market_alarm import live_alarm
    except ImportError:
        return None
    return lambda d, now: live_alarm(d, now)


def build(now: datetime, listings: Iterable[dict], alarm=None) -> tuple[list[dict], list[date], list[date]]:
    today = now.astimezone(ET).date()
    back, ahead = window(today)
    rows = listing_rows(listings, back, ahead, now, mega_from(alarm, now)) + alarm_rows(alarm, ahead, now)
    return rows, back, ahead


def publish(store, rows: list[dict], back: list[date], ahead: list[date], now: datetime) -> dict:
    """Upsert `rows`; withdraw the rows of the window this job wrote before and no longer finds."""
    lo, hi = (back or ahead)[0].isoformat(), ahead[-1].isoformat()
    if rows:
        store.upsert(TABLE, [{**r, "updated_at": now.isoformat()} for r in rows], "id")
    keep = {r["id"] for r in rows}
    old = store.select(TABLE, {"select": "id", "withdrawn": "is.false", "and": f"(session.gte.{lo},session.lte.{hi})"})
    gone = sorted(o["id"] for o in old if o["id"] not in keep)
    for nid in gone:
        store.update(TABLE, {"id": nid}, {"withdrawn": True, "updated_at": now.isoformat()})
    return {"written": len(rows), "withdrawn": gone}


def publish_as_helper(session, rows: list[dict], back: list[date], ahead: list[date], now: datetime) -> dict:
    """`publish` through migration 0039's `kb_helper_publish_notices` (one call: the upsert and the withdrawal of
    the window's rows no longer found), as the signed-in helper. Same result shape as `publish`."""
    lo, hi = (back or ahead)[0].isoformat(), ahead[-1].isoformat()
    res = session.rpc("kb_helper_publish_notices", {"p_rows": rows, "p_lo": lo, "p_hi": hi}) or {}
    return {"written": int(res.get("written") or 0), "withdrawn": list(res.get("withdrawn") or [])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    env = os.environ.get("KAIBOT_ENV_FILE") or str(Path.home() / "Projects/kaibot-secrets/.env")
    ap.add_argument("--env", default=env)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--now", help="ISO time with a zone (default: now); for a replay of an earlier morning")
    ap.add_argument("--write", action="store_true", help="write to Supabase (default: print only)")
    ap.add_argument("--helper-env", default=None, help="the helpers' env file (docs/ops/helper_role.md)")
    a = ap.parse_args(argv)
    now = datetime.fromisoformat(a.now.replace("Z", "+00:00")) if a.now else datetime.now(UTC)
    if now.tzinfo is None:
        ap.error("--now needs a time zone")

    from kaibot.ingest.listings_pit import Listings

    table = Listings.load(Path(a.data)).table(now, live=True)
    listings = table.astype(object).where(table.notna(), None).to_dict("records") if len(table) else []
    alarm = load_alarm()
    rows, back, ahead = build(now, listings, alarm)
    print(f"notices: {len(rows)} for {(back or ahead)[0]}..{ahead[-1]}"
          f"{'' if alarm else ' (no market alarm module yet: listings only)'}")
    for r in rows:
        print(f"  {r['session']}  {r['kind']:<11} {r['tier']:<6} {r['title']}")
    if not a.write:
        print("dry run: nothing written (add --write)")
        return 0

    from kaibot.common.helper_session import HelperSession, helper_env
    from kaibot.engine.config import DEFAULT_SUPABASE_URL, load_env_file

    henv = helper_env(a.helper_env)
    if henv is not None:  # M10 1.26 (#451): the helper sign-in; the service key is never read
        session = HelperSession.from_env(henv, DEFAULT_SUPABASE_URL, timeout_s=20.0)
        store = None
    else:
        from kaibot.engine.store import Store

        load_env_file(a.env)
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not key:
            print("no helpers' env file and SUPABASE_SERVICE_KEY is not set: nothing written", file=sys.stderr)
            return 1
        print("still on the service key (no helpers' env file; docs/ops/helper_role.md)", file=sys.stderr)
        store = Store((os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL).rstrip("/"), key).quick(20.0)
    try:
        res = publish(store, rows, back, ahead, now) if store is not None else \
            publish_as_helper(session, rows, back, ahead, now)
    except Exception as e:  # noqa: BLE001 - e.g. migration 0035 not applied yet
        print(f"not written: {str(e)[:200]}", file=sys.stderr)
        return 1
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
