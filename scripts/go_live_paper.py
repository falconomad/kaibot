#!/usr/bin/env python3
"""Helpers of scripts/go_live_paper.sh (the trend_momentum paper go-live, decision 15 section 11). Standard library
plus the repository's own momentum calendar; never prints a value of the .env.

    go_live_paper.py set-flag <env file> KAIBOT_MULTIDAY on    add or replace one line, timestamped backup first
    go_live_paper.py summary <check output file>               the plain summary after ./scripts/run_engine.sh --check
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
NY = ZoneInfo("America/New_York")
LEVEL_NAMES = {"conservative": "Careful", "balanced": "Balanced", "aggressive": "Hunter"}


def set_flag(path: Path, key: str, value: str, now: datetime | None = None) -> str:
    """Set `key=value` in the .env: replace every existing `key=` line (the last one wins in every loader, so the
    file ends with exactly one), else append it. Nothing to change: the file is left alone ("unchanged"). Otherwise a
    copy `<file>.bak.<YYYYmmdd-HHMMSS>` (same permissions) is written first ("changed <backup name>"). No line of the
    file is ever printed."""
    path = Path(path)
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or "\n" in value:
        raise SystemExit(f"bad setting {key!r}")
    text = path.read_text()
    lines = text.splitlines()
    want = f"{key}={value}"
    pat = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    hits = [i for i, x in enumerate(lines) if pat.match(x)]
    if len(hits) == 1 and lines[hits[0]].strip() == want:
        return "unchanged"
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)
    os.chmod(backup, path.stat().st_mode & 0o777)
    out = [x for i, x in enumerate(lines) if i not in hits[:-1]] if hits else list(lines)
    if hits:
        out[hits[-1] - (len(hits) - 1)] = want
    else:
        out.append(want)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n")
    os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)
    return f"changed (backup {backup.name})"


def check_report(text: str) -> dict:
    """The JSON report of `python -m kaibot.engine --check` inside its output (other lines around it)."""
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[i:])
            if isinstance(obj, dict) and "multi_day" in obj:
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return {}


def sessions_from(d: date, n: int = 15) -> list[date]:
    out, x = [], d
    while len(out) < n:
        if x.weekday() < 5:
            out.append(x)
        x += timedelta(days=1)
    return out


def next_decision(now: datetime) -> tuple[date, date]:
    """(decision evening, fill session): the first fill session after now for which trend_momentum decides
    (strategies/momentum.is_weekly_decision: the first session of its week on Wednesday or later), and the session
    whose close it decides after. Weekdays only (a market holiday moves it a session)."""
    from kaibot.strategies.momentum import is_weekly_decision

    et = now.astimezone(NY)
    start = et.date() + timedelta(days=1)
    days = sessions_from(et.date() - timedelta(days=7), 30)
    for f in [d for d in days if d >= start]:
        prev = max(d for d in days if d < f)
        if is_weekly_decision(prev, f):
            return prev, f
    raise RuntimeError("no decision day found")


def summary(text: str, now: datetime | None = None, shorting: str = "off") -> list[str]:
    rep = check_report(text)
    md = rep.get("multi_day") or {}
    if isinstance(md, str):  # "FAILED: ..."
        md = {}
    level = md.get("level") or "balanced"
    exp = (md.get("experiments") or [{}])[0]
    cap = (md.get("overnight_cap_pct") or {}).get("trend_momentum")
    dec, fill = next_decision(now or datetime.now(NY))
    ok = "ALL GOOD" in text
    rules = ", ".join(md.get("rules_loaded") or []) or "none"
    last = (md.get("daily_store") or {}).get("last_session")
    daily = (f"the store holds sessions up to {last}" if last else
             "not there yet: the service's daily job fills it now (logs/fetch_daily.log, can take a while)")
    out = [
        f"Check:            {'ALL GOOD' if ok else 'NOT GOOD: read the --check output above'}",
        f"Shorting:         {shorting} at the Alpaca paper account",
        f"Multi-day:        {md.get('KAIBOT_MULTIDAY', '?')} (rules: {rules})",
        f"Overnight:        {'allowed' if md.get('allow_overnight') else 'NOT allowed'}",
        f"Level:            {LEVEL_NAMES.get(level, level)}; trend_momentum may hold up to "
        f"{exp.get('max_exposure_pct', cap)}% of the run (overnight cap {cap}%)",
        f"Stop line:        the rule is retired if its book falls {exp.get('retire_drawdown_pct')}% from its peak",
        f"Next decision:    {dec:%A %d %b} after the close (bars complete 20:15 New York), for {fill:%A}",
        f"First buys:       {fill:%A %d %b}, in the closing auction (16:00 New York), if the rule picks any",
        f"Daily bars:       {daily}",
        "To stop:          ./scripts/service.sh hold     (Flatten and stop on the dashboard also sells everything)",
    ]
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "set-flag":
        print(set_flag(Path(argv[1]), argv[2], argv[3]))
        return 0
    if len(argv) >= 2 and argv[0] == "summary":
        now = os.environ.get("GO_LIVE_NOW_ISO")
        print("\n".join(summary(Path(argv[1]).read_text(), datetime.fromisoformat(now) if now else None)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
