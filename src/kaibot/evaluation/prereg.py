"""Pre-registration of a rule before its first run (decision 15 sections 5 and 7, Gate A).

A rule's reasons and settings are written down and committed BEFORE any test of it runs, so the test cannot
shape them. The file is `docs/prereg/<rule>.md`, copied from `docs/prereg/TEMPLATE.md`: hypothesis, who is on
the other side of the trade, the published evidence, frozen settings, at most five neighbours for the plateau
check, fill model, pass marks, date.

`require(rule)` is the one call a walk-forward makes before it runs anything:

    prereg_sha = prereg.require(args.rule)     # raises PreregError with a plain message, or returns the sha256

and it logs that hash with the trial row and the report. It fails when the file is missing, still has template
placeholders (`<<...>>`), lacks a section, names more than five neighbours, or (in a git checkout) is not
committed exactly as it is on disk. The hash is of the committed file, so any later edit shows.

A pre-registration written AFTER the first run is marked `Status: retroactive`. `require` accepts it (the run
is logged with its hash) and `Prereg.retroactive` is true: gate v2 never lets such a rule pass Gate B.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PREREG_DIR = Path("docs") / "prereg"
MAX_NEIGHBOURS = 5
SECTIONS = {  # key -> how the heading starts (case-insensitive)
    "hypothesis": "hypothesis",
    "other_side": "who is on the other side",
    "evidence": "published evidence",
    "settings": "frozen settings",
    "neighbours": "neighbours",
    "fill_model": "fill model",
    "pass_marks": "pass marks",
}
_PLACEHOLDER = re.compile(r"<<.*?>>|<<|>>", re.S)
_FIELD = re.compile(r"^\s*[-*]\s*\*\*(?P<key>[^:*]+):\*\*\s*(?P<value>.*?)\s*$", re.M)
_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(?P<item>.+?)\s*$", re.M)


class PreregError(RuntimeError):
    """The rule may not run: its pre-registration is missing, unfinished or not committed."""


@dataclass(frozen=True)
class Prereg:
    rule: str
    path: Path
    sha256: str  # of the file's bytes
    status: str  # "pre-registered" | "retroactive"
    date: date
    neighbours: tuple[str, ...]
    sections: dict[str, str]
    committed_at: datetime | None  # last commit that touched the file; None when git was not asked

    @property
    def retroactive(self) -> bool:
        return self.status == "retroactive"

    def summary(self) -> dict:
        return {"rule": self.rule, "sha256": self.sha256, "status": self.status, "date": self.date.isoformat(),
                "neighbours": list(self.neighbours),
                "committed_at": self.committed_at.isoformat() if self.committed_at else None}


def path_for(rule: str, root: Path | str | None = None) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", rule or ""):
        raise PreregError(f"{rule!r} is not a rule name (lower-case letters, digits and underscores)")
    return Path(root or ROOT) / PREREG_DIR / f"{rule}.md"


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    parts = re.split(r"^##\s+(.+?)\s*$", text, flags=re.M)
    for title, body in zip(parts[1::2], parts[2::2], strict=False):
        for key, start in SECTIONS.items():
            if title.strip().lower().startswith(start):
                out[key] = body.strip()
    return out


def _neighbour_names(body: str) -> tuple[str, ...]:
    names = []
    for m in _ITEM.finditer(body):
        item = m.group("item")
        if item.lower().startswith("none"):  # "- none": the rule has no neighbours
            continue
        tick = re.match(r"`([^`]+)`", item)
        names.append(tick.group(1) if tick else item.split(":")[0].strip())
    return tuple(names)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)


def _check_committed(path: Path, root: Path) -> datetime:
    rel = path.relative_to(root).as_posix()
    try:
        tracked = _git(root, "ls-files", "--error-unmatch", "--", rel)
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError) as e:
        raise PreregError(f"cannot check that {rel} is committed (git did not run: {e})") from None
    if inside.returncode != 0:
        raise PreregError(f"cannot check that {rel} is committed: {root} is not a git checkout. Run the test "
                          "from a clone of the repository.")
    if tracked.returncode != 0:
        raise PreregError(f"{rel} exists but is not committed. Commit it before the first run: the hash that is "
                          "logged must be the hash of a committed file.")
    if _git(root, "status", "--porcelain", "--", rel).stdout.strip():
        raise PreregError(f"{rel} has changes that are not committed. Commit them (or undo them) before the run.")
    when = _git(root, "log", "-1", "--format=%cI", "--", rel).stdout.strip()
    if not when:
        raise PreregError(f"{rel} is staged but has never been committed. Commit it before the first run.")
    return datetime.fromisoformat(when)


def load(rule: str, root: Path | str | None = None, *, committed: bool = True) -> Prereg:
    """Read and check `docs/prereg/<rule>.md`. Raises PreregError with what is wrong and how to fix it."""
    root = Path(root or ROOT)
    path = path_for(rule, root)
    if not path.is_file():
        raise PreregError(
            f"no pre-registration for rule '{rule}': {path.relative_to(root)} does not exist. Copy "
            f"{PREREG_DIR / 'TEMPLATE.md'}, fill in every section, commit it, then run the test "
            "(decision 15: a rule needs a pre-registration before its first run).")
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    rel = path.relative_to(root)
    left = _PLACEHOLDER.findall(text)
    if left:
        shown = ", ".join(repr(" ".join(x.split())[:50]) for x in left[:3])
        raise PreregError(f"{rel} still has {len(left)} template placeholder(s), e.g. {shown}. Replace every "
                          "<<...>> with the rule's own text before the first run.")
    fields = {m.group("key").strip().lower(): m.group("value").strip().strip("`") for m in _FIELD.finditer(text)}
    if fields.get("rule") != rule:
        raise PreregError(f"{rel}: the 'Rule' line says {fields.get('rule')!r}, the file is for '{rule}'.")
    status = (fields.get("status") or "").lower()
    if status.startswith("retroactive"):
        status = "retroactive"
    elif status.startswith("pre-registered"):
        status = "pre-registered"
    else:
        raise PreregError(f"{rel}: 'Status' must be 'pre-registered' or 'retroactive', not "
                          f"{fields.get('status')!r}.")
    try:
        when = date.fromisoformat((fields.get("date") or "")[:10])
    except ValueError:
        raise PreregError(f"{rel}: 'Date' must be a date like 2026-11-12, not {fields.get('date')!r}.") from None
    sections = _sections(text)
    missing = [start for key, start in SECTIONS.items() if not sections.get(key)]
    if missing:
        raise PreregError(f"{rel}: these sections are missing or empty: {', '.join(missing)}.")
    neighbours = _neighbour_names(sections["neighbours"])
    if len(neighbours) > MAX_NEIGHBOURS and status != "retroactive":
        raise PreregError(f"{rel} names {len(neighbours)} neighbours; at most {MAX_NEIGHBOURS} are allowed for "
                          "the plateau check (decision 15, Gate A).")
    committed_at = _check_committed(path, root) if committed else None
    return Prereg(rule, path, hashlib.sha256(raw).hexdigest(), status, when, neighbours, sections, committed_at)


def require(rule: str, root: Path | str | None = None, *, committed: bool = True) -> str:
    """sha256 of the rule's committed pre-registration, or PreregError when the rule may not run yet.

    `committed=False` skips the git check (unit tests on a temporary folder); real runs leave it on."""
    return load(rule, root, committed=committed).sha256
