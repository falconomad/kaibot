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

Two layouts are accepted. The template's (a `Rule`/`Status`/`Date` list, one heading per section) and the
long numbered layout of `docs/prereg/trend_momentum.md` (the rule in the title, "## 1. Hypothesis, and who is
on the other side", "## 2. Frozen construction", neighbours as a table, sources inside section 1, a date like
"12 November 2026"). A heading may cover several sections. A file without a `Status` line counts as
pre-registered.

Amendments (issue #161). A pre-registration may be amended only by APPENDING dated sections headed
"## Amendment <n> (...)", each committed. `load` reads the file's git history: `versions` holds the hash of every
committed version, oldest first, and whether today's file is that version plus amendments only;
`first_committed_at` is the first commit. `accepts(sha)` is true for today's hash and for every earlier version
that today's file only amends, so trial rows logged before an amendment stay attributable to the rule (gate v2
reports them as `amended_since_logged_runs`); any other edit of the text makes a logged hash unknown.

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
SECTIONS = {  # key -> the name used in messages (the template's heading)
    "hypothesis": "hypothesis",
    "other_side": "who is on the other side",
    "evidence": "published evidence",
    "settings": "frozen settings",
    "neighbours": "neighbours",
    "fill_model": "fill model",
    "pass_marks": "pass marks",
}
_ALIASES = {  # key -> what a heading may contain (case-insensitive, after a leading "1." is dropped)
    "hypothesis": ("hypothesis",),
    "other_side": ("other side",),
    "evidence": ("published evidence", "evidence"),
    "settings": ("frozen settings", "frozen construction"),
    "neighbours": ("neighbours", "neighbors"),
    "fill_model": ("fill model",),
    "pass_marks": ("pass marks",),
}
_SOURCES = re.compile(r"^(?:Sources?:|Source of the construction:).*?(?=\n\s*\n|\Z)", re.M | re.S)
_TITLE_RULE = re.compile(r"^#\s+Pre-registration:\s*`?(?P<rule>[a-z][a-z0-9_]*)`?", re.M)
_TABLE_ROW = re.compile(r"^\s*\|(?P<cells>.+)\|\s*$", re.M)
_PLACEHOLDER = re.compile(r"<<.*?>>|<<|>>", re.S)
_FIELD = re.compile(r"^\s*[-*]\s*\*\*(?P<key>[^:*]+):\*\*\s*(?P<value>.*?)\s*$", re.M)
_AMENDMENT = re.compile(r"^##\s+Amendment\s+\d+\b.*$", re.M)
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
    first_committed_at: datetime | None = None  # first commit of the file (the pre-registration itself)
    versions: tuple[tuple[str, datetime, bool], ...] = ()  # (sha256, committed_at, amended_only), oldest first
    amendments: tuple[str, ...] = ()  # the "## Amendment n" headings of today's file

    @property
    def retroactive(self) -> bool:
        return self.status == "retroactive"

    def accepts(self, sha: str) -> bool:
        """`sha` is today's file, or an earlier committed version that today's file only amends."""
        return sha == self.sha256 or any(v == sha and amended for v, _, amended in self.versions)

    def summary(self) -> dict:
        return {"rule": self.rule, "sha256": self.sha256, "status": self.status, "date": self.date.isoformat(),
                "neighbours": list(self.neighbours),
                "committed_at": self.committed_at.isoformat() if self.committed_at else None,
                "first_committed_at": self.first_committed_at.isoformat() if self.first_committed_at else None,
                "amendments": list(self.amendments),
                "versions": [{"sha256": v, "committed_at": t.isoformat(), "amended_only": a}
                             for v, t, a in self.versions]}


def path_for(rule: str, root: Path | str | None = None) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", rule or ""):
        raise PreregError(f"{rule!r} is not a rule name (lower-case letters, digits and underscores)")
    return Path(root or ROOT) / PREREG_DIR / f"{rule}.md"


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    parts = re.split(r"^##\s+(.+?)\s*$", text, flags=re.M)
    for title, body in zip(parts[1::2], parts[2::2], strict=False):
        head = re.sub(r"^\d+[.)]?\s*", "", title.strip().lower())
        for key, aliases in _ALIASES.items():
            if key not in out and any(a in head for a in aliases):
                out[key] = body.strip()
    if not out.get("evidence"):  # long layout: the sources are a paragraph of the hypothesis section
        found = [m.group(0) for b in out.values() for m in _SOURCES.finditer(b) if "docs/research/" in m.group(0)]
        if found:
            out["evidence"] = "\n\n".join(found)
    return out


def _neighbour_names(body: str) -> tuple[str, ...]:
    names = []
    rows = [m.group("cells") for m in _TABLE_ROW.finditer(body)]
    for cells in rows[1:]:  # a table: the first row is the header; a row of dashes separates it
        first = cells.split("|")[0].strip()
        if not first or set(first) <= set("-: "):
            continue
        tick = re.match(r"`([^`]+)`", first)
        names.append(tick.group(1) if tick else first)
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


def amended_only(old: str, new: str) -> bool:
    """`new` is `old` with dated "## Amendment n" sections appended and nothing else changed."""
    if old == new:
        return True
    if not new.startswith(old.rstrip()):
        return False
    tail = new[len(old.rstrip()):]
    if not tail.startswith("\n"):
        return False  # an amendment starts on a line of its own, never glued onto the last line
    rest = tail.strip()
    heads = [ln for ln in rest.splitlines() if ln.startswith("#")]
    return bool(rest) and rest.startswith("## Amendment") and all(_AMENDMENT.match(h) for h in heads)


def _versions(path: Path, root: Path, text: str) -> tuple[tuple[str, datetime, bool], ...]:
    """Every committed version of the file, oldest first: (sha256, commit time, today's file only amends it)."""
    rel = path.relative_to(root).as_posix()
    log = _git(root, "log", "--format=%H %cI", "--", rel).stdout.split("\n")
    out = []
    for line in reversed([x for x in log if x.strip()]):
        commit, when = line.split(" ", 1)
        raw = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{rel}"], capture_output=True,
                             timeout=20).stdout
        out.append((hashlib.sha256(raw).hexdigest(), datetime.fromisoformat(when.strip()),
                    amended_only(raw.decode("utf-8", errors="replace"), text)))
    return tuple(out)


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    m = re.match(r"\s*(\d{1,2} [A-Za-z]+ \d{4})", value)
    try:
        return datetime.strptime(m.group(1), "%d %B %Y").date() if m else None
    except ValueError:
        return None


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
    if "rule" not in fields and (title := _TITLE_RULE.search(text)):
        fields["rule"] = title.group("rule")
    if "status" not in fields:  # the long layout has no Status line: it is written before the first run
        fields["status"] = "pre-registered"
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
    when = _date(fields.get("date") or "")
    if when is None:
        raise PreregError(f"{rel}: 'Date' must start with a date like 2026-11-12 or 12 November 2026, not "
                          f"{fields.get('date')!r}.")
    sections = _sections(text)
    missing = [start for key, start in SECTIONS.items() if not sections.get(key)]
    if missing:
        raise PreregError(f"{rel}: these sections are missing or empty: {', '.join(missing)}.")
    neighbours = _neighbour_names(sections["neighbours"])
    if len(neighbours) > MAX_NEIGHBOURS and status != "retroactive":
        raise PreregError(f"{rel} names {len(neighbours)} neighbours; at most {MAX_NEIGHBOURS} are allowed for "
                          "the plateau check (decision 15, Gate A).")
    committed_at = _check_committed(path, root) if committed else None
    versions = _versions(path, root, text) if committed else ()
    return Prereg(rule, path, hashlib.sha256(raw).hexdigest(), status, when, neighbours, sections, committed_at,
                  versions[0][1] if versions else None, versions,
                  tuple(m.group(0).strip() for m in _AMENDMENT.finditer(text)))


def require(rule: str, root: Path | str | None = None, *, committed: bool = True) -> str:
    """sha256 of the rule's committed pre-registration, or PreregError when the rule may not run yet.

    `committed=False` skips the git check (unit tests on a temporary folder); real runs leave it on."""
    return load(rule, root, committed=committed).sha256
