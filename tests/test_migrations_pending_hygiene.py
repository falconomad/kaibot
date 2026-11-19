"""Hygiene (#361): `docs/ops/migrations_pending.md` and `migrations/` say the same thing.

The record of which production migrations are applied drifted twice in one week (a status report asked to approve
0023 and 0024, already applied; the summary line said 0029 and 0030 were pending after the list said applied). This
test reads only the repo: no database, no network.

Rules (docs/ops/hygiene.md, "Migration record"):
- every file in `migrations/` is `NNNN_<name>.sql`; the numbers are unique and run 0001, 0002, ... with no gap;
- the list under "## What production has now" has exactly one entry per file (`- NNNN: ...` or `- NNNN-MMMM: ...`),
  each saying `applied` or `pending` (`not applied`) first;
- the summary in the preamble, "Last checked against main on <date> (<set> applied; <set> pending)", agrees with
  the list (`none pending` when nothing is);
- every pending migration has a step in "## Apply in this order"; steps run 1, 2, ... once each, name a file that
  exists, and a list entry's "step N" names the row of its own migration;
- a migration the list calls applied has no `-- NOT APPLIED` line in its header comment, and a pending one has no
  `-- APPLIED` line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
DOC = ROOT / "docs" / "ops" / "migrations_pending.md"

# Applied migrations whose header still says "NOT APPLIED" on main when this test landed (19 Nov 2026). The header
# fix is a comment-only change in migrations/ (Backend-quant's folder), handed off from #361. Only these may be
# stale; any NEW stale header fails. Remove a number here once its header is fixed (nothing fails if you don't).
KNOWN_STALE_HEADERS = frozenset({"0015", "0017", "0019", "0021", "0022", "0023", "0024"})

FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
ENTRY_RE = re.compile(r"^- (\d{4})(?:-(\d{4}))?: (.*)$")
STEP_REF_RE = re.compile(r"\bsteps? (\d+)(?:-(\d+))? below\b")
ROW_RE = re.compile(r"^\| (\d+) \| `migrations/([^`]+)`")
SUMMARY_RE = re.compile(r"Last checked against main on [^()]+\(([^()]*)\)")
NOT_APPLIED_RE = re.compile(r"^--\s*NOT APPLIED\b")
APPLIED_RE = re.compile(r"^--\s*APPLIED\b")


@dataclass
class Record:
    status: dict[str, str] = field(default_factory=dict)  # number -> "applied" | "pending"
    step_refs: dict[str, set[int]] = field(default_factory=dict)  # list entry's numbers -> steps it names
    entry_groups: list[set[str]] = field(default_factory=list)
    steps: dict[int, str] = field(default_factory=dict)  # step -> file name
    summary: dict[str, set[str]] | None = None
    problems: list[str] = field(default_factory=list)


def _numbers(text: str) -> set[str]:
    """'0001-0028', '0029 and 0030', '0029, 0030', 'none' -> the set of four-digit numbers."""
    text = text.strip()
    if text == "none":
        return set()
    out: set[str] = set()
    for part in re.split(r",\s*|\s+and\s+", text):
        m = re.fullmatch(r"(\d{4})(?:-(\d{4}))?", part.strip())
        if not m:
            raise ValueError(f"cannot read {part!r} as a migration number or range")
        lo, hi = int(m[1]), int(m[2] or m[1])
        out |= {f"{n:04d}" for n in range(lo, hi + 1)}
    return out


def _section(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return []
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def parse(doc: str) -> Record:
    rec = Record()
    preamble = doc.split("\n## ", 1)[0]
    m = SUMMARY_RE.search(" ".join(preamble.split()))
    if not m:
        rec.problems.append("summary: no 'Last checked against main on <date> (<set> applied; <set> pending)'")
    else:
        rec.summary = {"applied": set(), "pending": set()}
        for part in m[1].split(";"):
            pm = re.fullmatch(r"\s*(.+?)\s+(applied|pending)\s*", part)
            if not pm:
                rec.problems.append(f"summary: cannot read {part.strip()!r}")
                continue
            try:
                rec.summary[pm[2]] |= _numbers(pm[1])
            except ValueError as e:
                rec.problems.append(f"summary: {e}")

    status_lines = _section(doc, "## What production has now")
    if not status_lines:
        rec.problems.append("list: no '## What production has now' section")
    for line in status_lines:
        em = ENTRY_RE.match(line)
        if not em:
            continue
        lo, hi, rest = em[1], em[2] or em[1], em[3]
        nums = _numbers(f"{lo}-{hi}")
        word = rest.lstrip("*").lower()
        if word.startswith("applied"):
            status = "applied"
        elif word.startswith(("pending", "not applied")):
            status = "pending"
        else:
            rec.problems.append(f"list: {lo}: status must start with 'applied' or 'pending', got {rest[:40]!r}")
            continue
        for n in sorted(nums):
            if n in rec.status:
                rec.problems.append(f"list: {n} has more than one entry")
            rec.status[n] = status
        rec.entry_groups.append(nums)
        refs: set[int] = set()
        for sm in STEP_REF_RE.finditer(rest):
            refs |= set(range(int(sm[1]), int(sm[2] or sm[1]) + 1))
        if refs:
            rec.step_refs[",".join(sorted(nums))] = refs

    for line in _section(doc, "## Apply in this order"):
        rm = ROW_RE.match(line)
        if not rm:
            continue
        step = int(rm[1])
        if step in rec.steps:
            rec.problems.append(f"steps: step {step} appears twice")
        rec.steps[step] = rm[2]
    return rec


def header(sql: str) -> list[str]:
    """The comment block at the top of a migration file (up to its first statement)."""
    out = []
    for line in sql.splitlines():
        if line.strip() and not line.lstrip().startswith("--"):
            break
        out.append(line.strip())
    return out


def check(doc: str, files: dict[str, str], known_stale: frozenset[str] = frozenset()) -> list[str]:
    """Every disagreement between the record and the files ({file name: file text})."""
    rec = parse(doc)
    problems = list(rec.problems)

    numbered: dict[str, list[str]] = {}
    for name in sorted(files):
        fm = FILE_RE.match(name)
        if not fm:
            problems.append(f"files: {name} is not NNNN_<lowercase_name>.sql")
            continue
        numbered.setdefault(fm[1], []).append(name)
    for n, names in numbered.items():
        if len(names) > 1:
            problems.append(f"files: number {n} is used by {', '.join(names)}")
    nums = sorted(numbered)
    expected = [f"{i:04d}" for i in range(1, len(nums) + 1)]
    if nums != expected:
        problems.append(f"files: numbers must run 0001-{len(nums):04d} without gaps; missing "
                        f"{sorted(set(expected) - set(nums))}, extra {sorted(set(nums) - set(expected))}")

    for n in nums:
        if n not in rec.status:
            problems.append(f"list: {numbered[n][0]} has no entry")
    for n in sorted(set(rec.status) - set(numbered)):
        problems.append(f"list: {n} has an entry but no file in migrations/")

    if rec.summary is not None:
        applied = {n for n, s in rec.status.items() if s == "applied"}
        pending = {n for n, s in rec.status.items() if s == "pending"}
        if rec.summary["applied"] != applied or rec.summary["pending"] != pending:
            problems.append(f"summary: says applied {_fmt(rec.summary['applied'])}, pending "
                            f"{_fmt(rec.summary['pending'])}; the list says applied {_fmt(applied)}, "
                            f"pending {_fmt(pending)}")

    steps = sorted(rec.steps)
    if steps != list(range(1, len(steps) + 1)):
        problems.append(f"steps: must run 1-{len(steps)} without gaps, got {steps}")
    step_num: dict[int, str] = {}
    for step, name in rec.steps.items():
        if name not in files:
            problems.append(f"steps: step {step} names migrations/{name}, which does not exist")
        step_num[step] = name[:4]
    for n, s in sorted(rec.status.items()):
        if s == "pending" and n not in step_num.values():
            problems.append(f"steps: {n} is pending but has no step in 'Apply in this order'")
    for key, refs in rec.step_refs.items():
        group = set(key.split(","))
        for step in sorted(refs):
            if step not in step_num:
                problems.append(f"list: {key} names step {step}, which is not in the table")
            elif step_num[step] not in group:
                problems.append(f"list: {key} names step {step}, but step {step} is {rec.steps[step]}")

    for n, s in sorted(rec.status.items()):
        if n not in numbered:
            continue
        name = numbered[n][0]
        head = header(files[name])
        if s == "applied" and any(NOT_APPLIED_RE.match(h) for h in head) and n not in known_stale:
            problems.append(f"header: {name} says NOT APPLIED; the list says applied")
        if s == "pending" and any(APPLIED_RE.match(h) for h in head):
            problems.append(f"header: {name} says APPLIED; the list says pending")
    return problems


def _fmt(nums: set[str]) -> str:
    """{'0001', '0002', '0003', '0005'} -> '0001-0003,0005'."""
    runs: list[list[int]] = []
    for n in sorted(int(x) for x in nums):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ",".join(f"{a:04d}" if a == b else f"{a:04d}-{b:04d}" for a, b in runs) or "none"


def _repo_files() -> dict[str, str]:
    return {p.name: p.read_text() for p in MIGRATIONS.iterdir() if p.suffix == ".sql"}


# ---------- the repo ----------

def test_migrations_pending_agrees_with_the_migration_files():
    assert check(DOC.read_text(), _repo_files(), KNOWN_STALE_HEADERS) == []


def test_known_stale_headers_are_only_applied_migrations():
    rec = parse(DOC.read_text())
    assert {n for n in KNOWN_STALE_HEADERS if rec.status.get(n) != "applied"} == set()


# ---------- the checker catches each kind of drift ----------

GOOD_DOC = """# Pending production migrations

Owner: backend-quant. Last checked against main on 19 Nov 2026 (0001-0002 applied;
0003 pending). Only the stakeholder applies production migrations.

## What production has now

- 0001: applied.
- 0002: **applied by Product on 19 Nov 2026** (step 1 below).
- 0003: **pending** (step 2 below).

## Apply in this order

| # | What to apply | What it does | Supabase asks to confirm? |
|---|---------------|--------------|---------------------------|
| 1 | `migrations/0002_b.sql` (whole file) | b | No |
| 2 | `migrations/0003_c.sql` (whole file) | c | No |
"""
GOOD_FILES = {
    "0001_a.sql": "-- 0001: a.\ncreate table a (x int);\n",
    "0002_b.sql": "-- 0002: b.\n--\n-- Additive.\ncreate table b (x int);\n-- NOT APPLIED below the header is fine\n",
    "0003_c.sql": "-- 0003: c.\n--\n-- NOT APPLIED. The stakeholder applies it.\ncreate table c (x int);\n",
}


def test_the_example_is_clean():
    assert check(GOOD_DOC, GOOD_FILES) == []


def _swap(old: str, new: str) -> str:
    assert old in GOOD_DOC
    return GOOD_DOC.replace(old, new)


@pytest.mark.parametrize(("doc", "files", "expect"), [
    pytest.param(_swap("0001-0002 applied;\n0003 pending", "0001 applied; 0002 and 0003 pending"), GOOD_FILES,
                 "summary: says applied 0001, pending 0002-0003; the list says applied 0001-0002, pending 0003",
                 id="summary-disagrees"),
    pytest.param(_swap("0003 pending", "none pending"), GOOD_FILES, "summary: says applied", id="summary-misses"),
    pytest.param(_swap("Last checked against main", "Checked"), GOOD_FILES, "summary: no", id="summary-missing"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0004_d.sql": "create table d (x int);\n"},
                 "list: 0004_d.sql has no entry", id="file-without-entry"),
    pytest.param(GOOD_DOC, {k: v for k, v in GOOD_FILES.items() if k != "0003_c.sql"},
                 "list: 0003 has an entry but no file", id="entry-without-file"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0003_other.sql": "create table e (x int);\n"},
                 "files: number 0003 is used by", id="duplicate-number"),
    pytest.param(_swap("- 0003: **pending** (step 2 below).", "- 0003: **pending** (step 2 below).\n- 0005: pending."),
                 {**GOOD_FILES, "0005_e.sql": "create table e (x int);\n"},
                 "files: numbers must run 0001-0004 without gaps", id="gap-in-numbers"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0004-d.sql": "x"}, "files: 0004-d.sql is not", id="bad-file-name"),
    pytest.param(_swap("- 0001: applied.", "- 0001: applied.\n- 0001: applied again."), GOOD_FILES,
                 "list: 0001 has more than one entry", id="entry-twice"),
    pytest.param(_swap("- 0001: applied.", "- 0001: done."), GOOD_FILES,
                 "list: 0001: status must start with", id="unreadable-status"),
    pytest.param(_swap("(step 1 below)", "(step 2 below)"), GOOD_FILES,
                 "list: 0002 names step 2, but step 2 is 0003_c.sql", id="step-ref-mismatch"),
    pytest.param(_swap("(step 2 below)", "(step 7 below)"), GOOD_FILES,
                 "list: 0003 names step 7, which is not in the table", id="step-ref-missing"),
    pytest.param(_swap("| 2 | `migrations/0003_c.sql`", "| 3 | `migrations/0003_c.sql`"), GOOD_FILES,
                 "steps: must run 1-2 without gaps", id="step-gap"),
    pytest.param(_swap("| 2 | `migrations/0003_c.sql`", "| 1 | `migrations/0003_c.sql`"), GOOD_FILES,
                 "steps: step 1 appears twice", id="step-twice"),
    pytest.param(_swap("`migrations/0003_c.sql`", "`migrations/0003_typo.sql`"), GOOD_FILES,
                 "steps: step 2 names migrations/0003_typo.sql, which does not exist", id="step-file-missing"),
    pytest.param(_swap("| 2 | `migrations/0003_c.sql` (whole file) | c | No |\n", ""), GOOD_FILES,
                 "steps: 0003 is pending but has no step", id="pending-without-step"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0002_b.sql": "-- 0002: b.\n-- NOT APPLIED. Apply on 'apply'.\nselect 1;\n"},
                 "header: 0002_b.sql says NOT APPLIED; the list says applied", id="stale-not-applied-header"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0003_c.sql": "-- 0003: c.\n-- APPLIED on 19 Nov.\nselect 1;\n"},
                 "header: 0003_c.sql says APPLIED; the list says pending", id="applied-header-but-pending"),
])
def test_the_checker_catches(doc, files, expect):
    problems = check(doc, files)
    assert any(p.startswith(expect) for p in problems), problems


def test_known_stale_headers_are_tolerated_but_new_ones_are_not():
    files = {**GOOD_FILES, "0002_b.sql": "-- 0002: b.\n-- NOT APPLIED.\nselect 1;\n"}
    assert check(GOOD_DOC, files, frozenset({"0002"})) == []
    assert check(GOOD_DOC, files, frozenset({"0001"})) != []


def test_number_sets_read_like_the_summary_line():
    assert _numbers("0001-0003") == {"0001", "0002", "0003"}
    assert _numbers("0029 and 0030") == {"0029", "0030"}
    assert _numbers("0029, 0030 and 0031") == {"0029", "0030", "0031"}
    assert _numbers("none") == set()
    with pytest.raises(ValueError):
        _numbers("0029 to 0030")
