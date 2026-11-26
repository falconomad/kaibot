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
- no migration header claims a status (`-- APPLIED ...` or `-- NOT APPLIED ...`): the status lives only in
  `docs/ops/migrations_pending.md`, because a header cannot be updated when Product applies the file (Product on
  #368; the seven stale headers of #361 were fixed there);
- no other runbook in `docs/ops/` calls a migration file `not applied` or `pending` when the record says applied
  (audit 2 ticket 5.6, #686: `failsafe.md` still said 0017 and 0019 were "not applied" two weeks after both were).

CI runs this file twice: inside the full suite and as its own read-only `migrations-record` job (ci.yml), so the
check can be required on `main` by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
DOC = ROOT / "docs" / "ops" / "migrations_pending.md"

FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
ENTRY_RE = re.compile(r"^- (\d{4})(?:-(\d{4}))?: (.*)$")
STEP_REF_RE = re.compile(r"\bsteps? (\d+)(?:-(\d+))? below\b")
ROW_RE = re.compile(r"^\| (\d+) \| `migrations/([^`]+)`")
SUMMARY_RE = re.compile(r"Last checked against main on [^()]+\(([^()]*)\)")
STATUS_RE = re.compile(r"^--\s*(NOT APPLIED|APPLIED)\b")
# a backticked migration file, then within the same clause (no `.`, `;` or `|`) "not applied", "not yet applied" or
# "pending": a status claim about that file
DOC_CLAIM_RE = re.compile(r"`(?:migrations/)?(\d{4})_[a-z0-9_]+\.sql`[^.;|`]{0,40}?\b(not (?:yet )?applied|pending)\b",
                          re.I)


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


def check(doc: str, files: dict[str, str]) -> list[str]:
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

    for n in nums:
        for name in numbered[n]:
            for h in header(files[name]):
                sm = STATUS_RE.match(h)
                if sm:
                    problems.append(f"header: {name} says {sm[1]}; a status lives only in migrations_pending.md")
    return problems


def other_doc_claims(status: dict[str, str], docs: dict[str, str]) -> list[str]:
    """Runbooks ({name: text}) that call a migration `not applied` or `pending` while the record says applied."""
    problems = []
    for name, text in sorted(docs.items()):
        for i, line in enumerate(text.splitlines(), 1):
            for m in DOC_CLAIM_RE.finditer(line):
                if status.get(m[1]) == "applied":
                    problems.append(f"docs: {name}:{i} says {m[1]} is {m[2].lower()}; "
                                    f"migrations_pending.md says applied")
    return problems


def _other_docs() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(DOC.parent.glob("*.md")) if p != DOC}


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
    assert check(DOC.read_text(), _repo_files()) == []


def test_no_runbook_calls_an_applied_migration_not_applied():
    """#686: failsafe.md said 0017 (and 0019) were "not applied"; the record says both were applied on 13 Nov 2026."""
    assert other_doc_claims(parse(DOC.read_text()).status, _other_docs()) == []


@pytest.mark.parametrize(("line", "found"), [
    ("migration `0001_a.sql` (**not applied yet; the stakeholder applies it**)", True),
    ("| `migrations/0002_b.sql`, **not applied**: the same plan in SQL |", True),
    ("`0002_b.sql` is pending", True),
    ("`0003_c.sql` (**not applied**)", False),  # pending in the record: a true claim
    ("`0001_a.sql` (applied 13 Nov 2026). Until 0047 is applied it is not applied yet", False),
    ("Until 0047 is applied it prints \"migration 0047 is not applied yet\"", False),  # no file named
    ("`0002_b.sql` | the dashboard waits for a pending command", False),  # another cell
])
def test_the_runbook_check_reads_a_claim_only_where_one_is_made(line, found):
    status = parse(GOOD_DOC).status
    assert bool(other_doc_claims(status, {"x.md": line})) is found


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
    "0003_c.sql": "-- 0003: c.\n--\n-- Status: docs/ops/migrations_pending.md.\ncreate table c (x int);\n",
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
                 "header: 0002_b.sql says NOT APPLIED", id="not-applied-header-on-an-applied-file"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0003_c.sql": "-- 0003: c.\n-- NOT APPLIED.\nselect 1;\n"},
                 "header: 0003_c.sql says NOT APPLIED", id="not-applied-header-even-when-pending"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0003_c.sql": "-- 0003: c.\n-- APPLIED on 19 Nov.\nselect 1;\n"},
                 "header: 0003_c.sql says APPLIED", id="applied-header-on-a-pending-file"),
    pytest.param(GOOD_DOC, {**GOOD_FILES, "0001_a.sql": "-- 0001: a.\n-- APPLIED by Product.\nselect 1;\n"},
                 "header: 0001_a.sql says APPLIED", id="applied-header-even-when-applied"),
])
def test_the_checker_catches(doc, files, expect):
    problems = check(doc, files)
    assert any(p.startswith(expect) for p in problems), problems


def test_no_header_in_the_repo_claims_a_status():
    """#368: the seven stale NOT APPLIED headers (0015, 0017, 0019, 0021-0024) and 0032's APPLIED line are gone."""
    claims = [(name, h) for name, text in sorted(_repo_files().items()) for h in header(text) if STATUS_RE.match(h)]
    assert claims == []


def test_number_sets_read_like_the_summary_line():
    assert _numbers("0001-0003") == {"0001", "0002", "0003"}
    assert _numbers("0029 and 0030") == {"0029", "0030"}
    assert _numbers("0029, 0030 and 0031") == {"0029", "0030", "0031"}
    assert _numbers("none") == set()
    with pytest.raises(ValueError):
        _numbers("0029 to 0030")
