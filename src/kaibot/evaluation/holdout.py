"""The hold-out window: history no research run may read (M11 ticket 2.6, issue #457; Product's decision D5).

Until now every ten-year test read every session up to the newest bar, so no stretch of history was left to
check a rule on data that had no part in choosing it. `config/holdout.yaml` now names a date, `holdout_from`;
every session on or after it is held back.

The rule, in one line: **a research run whose window reaches the hold-out fails.** The only way in is an
opening: an entry in `config/holdout.yaml` (committed by pull request before the run) that names the rule, the
purpose, the scripts and how many reads it allows. A run under an opening is logged twice: the opening itself is
in the repository's history, and each read appends a line to `<data>/holdout_access.jsonl` on the machine that
ran it (opening, rule, script, window, config hash, code version, time). If that line cannot be written the run
does not start.

How a script uses it (every research script in `scripts/` does; `tests/unit/test_holdout.py` checks the list):

    holdout.add_argument(ap)                                   # --open-holdout ID
    ...
    holdout.enforce(script=__file__, end=a.end, rule=RULE, opening=a.open_holdout, data=a.data)

`end` is the last date the run may read (`None`: the run reads to the newest bar). `enforce` exits with a plain
message when the run is refused, so a refused run is a failed command.

What this does not do, stated plainly:

- The window is frozen on 21 Nov 2026, after every existing rule had already been run on data up to November
  2026. For those rules, and for any new rule designed with knowledge of those results, the hold-out is not
  untouched data. It is clean only for what happens after the freeze date; each year that passes makes it
  cleaner. `docs/ops/holdout.md` says how a report must word this.
- It guards the research scripts, not the files. Someone who opens the parquet files by hand is not stopped;
  the guard removes the accidental read, which is the common one.
- The live paper engine is not a research run and is not touched: it trades today's sessions as before.

Standard library and PyYAML only. Nothing here is imported by the engine.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config") / "holdout.yaml"
ACCESS_LOG = "holdout_access.jsonl"  # under the data directory
ENV_OPENING = "KAIBOT_HOLDOUT_OPENING"
PURPOSES = ("final_check", "gate_c_tracking", "data_check")
OPENING_KEYS = ("id", "rule", "purpose", "scripts", "max_runs", "decided_by", "decided_on", "reason")


class HoldoutError(RuntimeError):
    """A research run asked for sessions inside the hold-out and may not read them."""


@dataclass(frozen=True)
class Opening:
    id: str
    rule: str
    purpose: str
    scripts: tuple[str, ...]
    max_runs: int
    decided_by: str
    decided_on: date
    reason: str


@dataclass(frozen=True)
class Holdout:
    start: date  # the first held-back date
    research_end: date  # the last date a research run may name
    sha: str  # sha256 of config/holdout.yaml
    openings: tuple[Opening, ...] = ()

    def holds(self, day: date | None) -> bool:
        """Whether a run that reads up to `day` reads the hold-out. `None` = up to the newest bar: it does."""
        return day is None or day >= self.start

    def opening(self, opening_id: str) -> Opening | None:
        return next((o for o in self.openings if o.id == opening_id), None)


def _day(value, what: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HoldoutError(f"{what}: {value!r} is not a date (YYYY-MM-DD)") from None


def from_bytes(raw: bytes) -> Holdout:
    cfg = yaml.safe_load(raw) or {}
    for key in ("holdout_from", "research_window_end", "openings"):
        if key not in cfg:
            raise HoldoutError(f"{CONFIG}: `{key}` is missing")
    start, end = _day(cfg["holdout_from"], "holdout_from"), _day(cfg["research_window_end"], "research_window_end")
    if not end < start:
        raise HoldoutError(f"{CONFIG}: research_window_end {end} must be before holdout_from {start}")
    openings, seen = [], set()
    for o in cfg["openings"] or []:
        missing = [k for k in OPENING_KEYS if k not in (o or {})]
        if missing:
            raise HoldoutError(f"{CONFIG}: opening {(o or {}).get('id', '?')!r} lacks {', '.join(missing)}")
        if o["purpose"] not in PURPOSES:
            raise HoldoutError(f"{CONFIG}: opening {o['id']!r}: purpose must be one of {', '.join(PURPOSES)}")
        if str(o["id"]) in seen:
            raise HoldoutError(f"{CONFIG}: opening id {o['id']!r} is listed twice")
        if isinstance(o["max_runs"], bool) or not isinstance(o["max_runs"], int) or o["max_runs"] < 1:
            raise HoldoutError(f"{CONFIG}: opening {o['id']!r}: max_runs must be a whole number of at least 1")
        if not o["scripts"] or not str(o["reason"]).strip():
            raise HoldoutError(f"{CONFIG}: opening {o['id']!r} needs its scripts and a reason")
        seen.add(str(o["id"]))
        openings.append(Opening(str(o["id"]), str(o["rule"]), o["purpose"], tuple(str(s) for s in o["scripts"]),
                                int(o["max_runs"]), str(o["decided_by"]), _day(o["decided_on"], "decided_on"),
                                str(o["reason"]).strip()))
    return Holdout(start, end, hashlib.sha256(raw).hexdigest(), tuple(openings))


def load(root: Path | str | None = None) -> Holdout:
    return from_bytes((Path(root or ROOT) / CONFIG).read_bytes())


def read_log(data: Path | str) -> list[dict]:
    """The access log of a data directory, oldest first. A line that cannot be read still counts as a read."""
    f = Path(data) / ACCESS_LOG
    if not f.is_file():
        return []
    rows = []
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"unreadable": line[:200]})
    return rows


def _code_sha(root: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=root,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check(end, *, script: str, rule: str | None, data: Path | str, opening: str | None = None,
          root: Path | str | None = None, now: datetime | None = None, argv: list[str] | None = None,
          holdout: Holdout | None = None) -> dict | None:
    """Allow or refuse one run. `end`: the last date it may read (a date, an ISO string, or None for "to the
    newest bar"). Returns None when the run stays outside the hold-out (nothing is logged), or the access-log
    row it wrote when it reads the hold-out under a valid opening. Raises `HoldoutError` otherwise."""
    h = holdout or load(root)
    last = None if end is None else _day(end, "end")
    name = Path(script).name
    if not h.holds(last):
        return None
    asked = "to the newest bar" if last is None else f"to {last}"
    from kaibot.common import switches  # M10 1.23 (#448)

    opening = opening or switches.get(ENV_OPENING) or None
    if not opening:
        raise HoldoutError(
            f"{name} would read {asked}, inside the hold-out (every session from {h.start} on is held back: "
            f"{CONFIG}). Use --end {h.research_end} or earlier. Reading the hold-out needs an opening committed "
            f"to {CONFIG} first, named with --open-holdout <id> (docs/ops/holdout.md).")
    o = h.opening(opening)
    if o is None:
        raise HoldoutError(f"{name}: opening {opening!r} is not in {CONFIG} (an opening is committed by pull "
                           "request before the run)")
    if name not in o.scripts:
        raise HoldoutError(f"{name}: opening {o.id!r} allows {', '.join(o.scripts)}, not this script")
    if rule is None or o.rule != rule:
        raise HoldoutError(f"{name}: opening {o.id!r} is for the rule {o.rule!r}, this run is {rule!r}")
    log_path = Path(data) / ACCESS_LOG
    used = sum(1 for r in read_log(data) if r.get("opening") == o.id or "unreadable" in r)
    if used >= o.max_runs:
        raise HoldoutError(f"{name}: opening {o.id!r} allows {o.max_runs} reads and {used} are logged in "
                           f"{log_path}: a further read needs a new opening")
    row = {"ts": (now or datetime.now(UTC)).isoformat(timespec="seconds"), "opening": o.id, "purpose": o.purpose,
           "rule": rule, "script": name, "end": None if last is None else last.isoformat(),
           "holdout_from": h.start.isoformat(), "holdout_sha256": h.sha, "read": used + 1, "max_runs": o.max_runs,
           "code_sha": _code_sha(Path(root or ROOT)), "argv": list(sys.argv[1:] if argv is None else argv)}
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as e:
        raise HoldoutError(f"{name}: the read could not be logged to {log_path} ({e}); nothing was read") from None
    print(f"HOLD-OUT OPENED: {name} reads {asked} under opening {o.id} ({o.purpose}, read {used + 1} of "
          f"{o.max_runs}); logged to {log_path}", file=sys.stderr, flush=True)
    return row


# ---------------------------------------------------------------- script glue


def add_argument(ap) -> None:
    ap.add_argument("--open-holdout", default=None, metavar="ID",
                    help=f"read the hold-out under this opening of {CONFIG} (logged; docs/ops/holdout.md)")


def enforce(*, script: str, end, rule: str | None, data: Path | str, opening: str | None = None,
            root: Path | str | None = None) -> dict | None:
    """`check` for a script: a refused run exits non-zero with the reason."""
    try:
        return check(end, script=script, rule=rule, data=data, opening=opening, root=root)
    except HoldoutError as e:
        sys.exit(f"hold-out: {e}")


def stamp(row: dict | None, holdout: Holdout | None = None) -> dict:
    """What a report or a trial row records about the hold-out: the config hash and, when it was opened, how."""
    h = holdout or load()
    out = {"holdout_from": h.start.isoformat(), "holdout_sha256": h.sha, "read": row is not None}
    if row is not None:
        out.update(opening=row["opening"], purpose=row["purpose"])
    return out
