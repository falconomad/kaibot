"""Does the Mac's daily store hold the point-in-time keys the live daily path reads (#812)? Read-only.

`./scripts/check_pit_store.sh` (scripts/check_pit_store.py) runs `check()` and prints `render()`. It loads the view
the way the live daily path does (`engine/live_daily.StoreLoader`, mode "pit": `daily_report.load_store` with the
live integrity gate), for the last session the store holds, and compares the store's keys with the keys
`sp500_pit.Membership.keys()` names (as-of dates pinned to `sp500_pit.STORE_DAY`).

No network, no writes, no broker: the store's parquet files and the committed membership data are only read.

The last line is one of
  OK: live path would trade N stocks
  FAIL: live path would stop (no orders) — missing keys          (a key mismatch: KeyMismatchError)
  FAIL: live path would stop (no orders) — <the loader's reason>  (any other reason the loader refuses the view)
  FAIL: live path would trade only N stocks — K current members have no key in the store
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from kaibot.ingest.sp500_pit import STORE_DAY, UNIVERSE, KeyMismatchError, Membership

MAX_LISTED = 20
MISSING_KEYS = "missing keys"


def key_report(m: Membership, store_keys: Iterable[str], cfg: Mapping) -> dict:
    """Expected, matched and missing keys of the pit view against the store's keys (pure, no I/O)."""
    have = set(store_keys)
    plain = [*cfg["sectors"], cfg["market"]]
    current = m.current()
    missing_current: list[dict] = []
    missing_former: list[dict] = []
    matched = 0
    for c in m.spells:
        keys = m.keys(c)
        if any(k in have for k in keys):
            matched += 1
            continue
        sym = m.request(c)[0]
        row = {"company": c, "expects": keys, "store_has": sorted(k for k in have if k.startswith(f"{sym}@"))}
        (missing_current if c in current else missing_former).append(row)
    try:
        m.key_map(sorted(have))
        mismatch = None
    except KeyMismatchError as e:
        mismatch = str(e)
    plain_missing = [k for k in plain if k not in have]
    return {"store_day": STORE_DAY.isoformat(),
            "companies_expected": len(m.spells), "current_members": len(current),
            "keys_expected": len(m.spells) + len(plain),
            "companies_matched": matched, "keys_matched": matched + len(plain) - len(plain_missing),
            "missing_current": missing_current, "missing_former": missing_former,
            "missing_plain": plain_missing, "mismatch": mismatch}


def live_view(root: Path, universe_file: Path, now: datetime | None = None) -> dict:
    """Load the evening view of the store's last session as `live_daily.StoreLoader` does. Returns
    {"session", "stocks", "members", "tradable", "dropped"} or {"session", "error"}; nothing is written."""
    import numpy as np

    from kaibot.engine import live_daily
    from kaibot.ingest.daily_store import DailyStore

    try:
        b = DailyStore(Path(root) / "daily").bars("SPY")
    except (FileNotFoundError, OSError) as e:
        return {"session": None, "error": f"no daily store at {Path(root) / 'daily'} ({type(e).__name__})"}
    if not len(b):
        return {"session": None, "error": "the daily store has no SPY bar"}
    k = b["date"].iloc[-1]
    k = k.date() if hasattr(k, "date") else k
    now = now or datetime.now(UTC)
    ts = b["available_ts"].iloc[-1] if "available_ts" in b else None
    if ts is not None and ts == ts:  # judge the keys, not the clock: read as on the evening the bar is complete
        import pandas as pd

        t = pd.Timestamp(ts)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        now = max(now, t.to_pydatetime())
    loader = live_daily.StoreLoader(Path(root), Path(universe_file))
    try:
        data = loader(k, now)
    except live_daily.DataNotReady as e:
        return {"session": str(k), "error": str(e)}
    cols = np.asarray(data.stocks, dtype=int)
    member = data.member[-1, cols] if data.member is not None else np.ones(len(cols), dtype=bool)
    dropped = {data.col(s) for s in data.dropped if s in data._col}
    ok = [j for j in cols[data.ok[-1, cols]].tolist() if j not in dropped]
    return {"session": str(k), "stocks": len(cols), "members": int(member.sum()), "tradable": len(ok),
            "dropped": dict(data.dropped)}


def check(root: Path, universe_file: Path = UNIVERSE, now: datetime | None = None) -> dict:
    import yaml

    from kaibot.ingest.daily_store import DailyStore

    cfg = yaml.safe_load(Path(universe_file).read_text())
    m = Membership.load(universe=Path(universe_file))
    try:
        keys = DailyStore(Path(root) / "daily").keys()
    except (FileNotFoundError, OSError):
        keys = []
    rep = key_report(m, keys, cfg)
    rep["store"] = str(Path(root) / "daily")
    rep["live"] = live_view(root, universe_file, now)
    rep["ok"], rep["verdict"] = verdict(rep)
    return rep


def verdict(rep: Mapping) -> tuple[bool, str]:
    live = rep["live"]
    if rep.get("mismatch") or "no matching store key" in str(live.get("error", "")):
        return False, f"FAIL: live path would stop (no orders) — {MISSING_KEYS}"
    if live.get("error"):
        return False, f"FAIL: live path would stop (no orders) — {live['error']}"
    if rep["missing_current"]:
        return False, (f"FAIL: live path would trade only {live['tradable']} stocks — "
                       f"{len(rep['missing_current'])} current members have no key in the store")
    return True, f"OK: live path would trade {live['tradable']} stocks"


def render(rep: Mapping) -> str:
    live = rep["live"]
    out = [f"Daily store: {rep['store']} (keys pinned to STORE_DAY {rep['store_day']}, #812)",
           f"Companies expected: {rep['companies_expected']} ({rep['current_members']} current members); "
           f"keys expected: {rep['keys_expected']}",
           f"Matched: {rep['companies_matched']} companies, {rep['keys_matched']} keys",
           f"Missing: {len(rep['missing_current'])} current members, {len(rep['missing_former'])} former members "
           "(former: the hole policy's no_bars, not an error), "
           f"{len(rep['missing_plain'])} ETF/SPY keys"]
    rows = [*rep["missing_current"], *rep["missing_former"]]
    for r in rows[:MAX_LISTED]:
        has = f"; the store has {', '.join(r['store_has'])}" if r["store_has"] else ""
        out.append(f"  - {r['company']}: expects {' or '.join(r['expects'])}{has}")
    if len(rows) > MAX_LISTED:
        out.append(f"  ... and {len(rows) - MAX_LISTED} more")
    for k in rep["missing_plain"]:
        out.append(f"  - {k} (plain key)")
    if live.get("error"):
        out.append(f"Live view of {live.get('session')}: refused: {live['error']}")
    else:
        out.append(f"Live view of {live['session']}: {live['stocks']} stocks loaded, {live['members']} members that "
                   f"session, {live['tradable']} tradable (price, liquidity, member, not dropped), "
                   f"{len(live['dropped'])} dropped by the integrity gate")
    out.append(rep["verdict"])
    return "\n".join(out)


def engine_warning(root: Path, universe_file: Path = UNIVERSE) -> dict | str:
    """For the engine's `--check`: the verdict as a warning line (never a FAILED line), or the summary numbers."""
    try:
        rep = check(root, universe_file)
    except Exception as e:  # a report only
        return f"WARNING: pit store check could not run: {str(e)[:160]}"
    if not rep["ok"]:
        return f"WARNING: {rep['verdict']} (run ./scripts/check_pit_store.sh)"
    return {"verdict": rep["verdict"], "companies_matched": rep["companies_matched"],
            "keys_matched": rep["keys_matched"], "keys_expected": rep["keys_expected"]}
