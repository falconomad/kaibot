#!/usr/bin/env python3
"""Helpers of scripts/go_live_paper.sh (the trend_momentum paper go-live, decision 15 section 11). Standard library
plus the repository's own momentum calendar; never prints a value of the .env.

    go_live_paper.py set-flag <env file> KAIBOT_MULTIDAY on    add or replace one line, timestamped backup first
    go_live_paper.py gate <check output file>                  exit 1 unless the check shows the experiment switched on
    go_live_paper.py summary <check output file>               the plain summary after ./scripts/run_engine.sh --check
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
LEVEL_NAMES = {"conservative": "Careful", "balanced": "Balanced", "aggressive": "Hunter"}


def set_flag(path: Path, key: str, value: str, now: datetime | None = None) -> str:
    """Set `key=value` in the .env: replace every existing `key=` line (the file ends with exactly one, so no loader
    can read another value), else append it. Every other line is kept byte for byte (line endings included).
    Nothing to change: the file is left alone ("unchanged"). Otherwise a copy `<file>.bak.<YYYYmmdd-HHMMSS>` (same
    permissions) is written first ("changed <backup name>"). No line of the file is ever printed, and no copy of it
    is ever readable by others, not even for a moment (QA-Ops second key on #218)."""
    path = Path(path).resolve()  # a symlinked .env: its target is edited, the link stays
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or "\n" in value or "\r" in value:
        raise SystemExit(f"bad setting {key!r}")
    data = path.read_bytes()
    lines = data.split(b"\n")
    want = f"{key}={value}".encode()
    pat = re.compile(rb"^\s*(export\s+)?" + re.escape(key.encode()) + rb"\s*=")
    hits = [i for i, x in enumerate(lines) if pat.match(x)]
    if len(hits) == 1 and lines[hits[0]].strip() == want:
        return "unchanged"
    mode = path.stat().st_mode & 0o777
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    _write_private(backup, data, mode)
    if hits:
        eol = b"\r" if lines[hits[-1]].endswith(b"\r") else b""
        lines[hits[-1]] = want + eol
        out = [x for i, x in enumerate(lines) if i not in hits[:-1]]
    else:
        out = list(lines)
        if out and out[-1] == b"":  # the file ended with a newline: the new line goes before the end
            crlf = len(out) > 1 and out[-2].endswith(b"\r")
            out.insert(len(out) - 1, want + (b"\r" if crlf else b""))
        else:
            out.append(want)
            out.append(b"")
    tmp = path.with_name(path.name + ".tmp")
    _write_private(tmp, b"\n".join(out), mode)
    os.replace(tmp, path)
    return f"changed (backup {backup.name})"


def _write_private(p: Path, data: bytes, mode: int) -> None:
    """Write `data` to `p`, created owner-only (0600) and then given `mode`: never readable by others in between."""
    with contextlib.suppress(FileNotFoundError):
        p.unlink()  # a leftover .tmp of an interrupted run
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(p, mode)


def gate(text: str) -> list[str]:
    """What the --check report must show before the service is installed: the multi-day switch on, overnight
    allowed and at least one multi-day rule loaded. Returns the problems (empty when all is right)."""
    md = check_report(text).get("multi_day")
    if not isinstance(md, dict):
        return ["the check has no multi_day report (FAILED or missing)"]
    out = []
    if md.get("KAIBOT_MULTIDAY") != "on":
        out.append("KAIBOT_MULTIDAY is not on for the engine (is it set to something else in the .env?)")
    if md.get("allow_overnight") is not True:
        out.append("allow_overnight is not true in the code the engine runs (an old release pinned?)")
    if not md.get("rules_loaded"):
        out.append("no multi-day rule is loaded")
    return out


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


def week_number(d: date) -> int:
    """strategies/momentum.week_number without numpy (Monday-based weeks since 1970)."""
    return ((d - date(1970, 1, 1)).days + 3) // 7


def is_weekly_decision(prev: date, fill: date) -> bool:
    """strategies/momentum.is_weekly_decision in the standard library only (this script must run before the
    engine's Python environment exists; a test keeps the two equal)."""
    late = fill.weekday() >= 2
    return late and not (prev.weekday() >= 2 and week_number(prev) == week_number(fill))


def next_decision(now: datetime) -> tuple[date, date]:
    """(decision evening, fill session): the first fill session after now for which trend_momentum decides (the
    first session of its week on Wednesday or later), and the session whose close it decides after. Weekdays only
    (a market holiday moves it a session)."""
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
    if len(argv) >= 2 and argv[0] == "gate":
        problems = gate(Path(argv[1]).read_text())
        for x in problems:
            print(f"NOT READY: {x}")
        return 1 if problems else 0
    if len(argv) >= 2 and argv[0] == "summary":
        now = os.environ.get("GO_LIVE_NOW_ISO")
        print("\n".join(summary(Path(argv[1]).read_text(), datetime.fromisoformat(now) if now else None)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
