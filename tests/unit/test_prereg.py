"""Pre-registration (issue #126, decision 15 Gate A): a rule cannot run before `docs/prereg/<rule>.md` is
complete and committed, and the hash of that file is what gets logged."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import prereg
from kaibot.evaluation.prereg import PreregError

TEMPLATE = ROOT / "docs" / "prereg" / "TEMPLATE.md"
FIRST_LOOK = ("trend_highs", "rel_strength", "winners_run", "overnight_spy")  # decision 15, section 5, row 0


def filled(rule: str = "my_rule", neighbours: int = 2, status: str = "pre-registered",
           day: str = "2026-11-12") -> str:
    """The real template with every placeholder replaced, so the template and the checker cannot drift apart."""
    t = TEMPLATE.read_text().replace("<<rule_name>>", rule)
    t = re.sub(r"<<YYYY-MM-DD.*?>>", day, t)
    items = "\n".join(f"- `n{i}`: one step from the frozen setting" for i in range(neighbours)) or "None."
    t = re.sub(r"(## Neighbours[^\n]*\n\n)<<.*?>>", lambda m: m.group(1) + items, t, flags=re.S)
    t = re.sub(r"<<.*?>>", "Filled in for the test.", t, flags=re.S)
    return t.replace("- **Status:** pre-registered", f"- **Status:** {status}")


def put(root: Path, rule: str, text: str) -> Path:
    f = root / "docs" / "prereg" / f"{rule}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


# ---------------------------------------------------------------- the file


def test_a_rule_without_a_pre_registration_may_not_run(tmp_path: Path):
    with pytest.raises(PreregError) as e:
        prereg.require("trend_momentum", tmp_path, committed=False)
    msg = str(e.value)
    assert "docs/prereg/trend_momentum.md does not exist" in msg and "TEMPLATE.md" in msg and "commit" in msg


def test_a_copy_of_the_template_is_not_a_pre_registration(tmp_path: Path):
    put(tmp_path, "my_rule", TEMPLATE.read_text().replace("<<rule_name>>", "my_rule"))
    with pytest.raises(PreregError, match="template placeholder"):
        prereg.require("my_rule", tmp_path, committed=False)
    put(tmp_path, "my_rule", filled().replace("Filled in for the test.", "to do <<later>>", 1))
    with pytest.raises(PreregError, match="1 template placeholder"):
        prereg.require("my_rule", tmp_path, committed=False)


def test_a_complete_file_gives_its_sha256(tmp_path: Path):
    f = put(tmp_path, "my_rule", filled())
    sha = prereg.require("my_rule", tmp_path, committed=False)
    assert sha == hashlib.sha256(f.read_bytes()).hexdigest() and len(sha) == 64
    p = prereg.load("my_rule", tmp_path, committed=False)
    assert (p.status, p.retroactive, p.neighbours, p.date.isoformat()) == \
        ("pre-registered", False, ("n0", "n1"), "2026-11-12")
    assert set(p.sections) == set(prereg.SECTIONS) and p.committed_at is None
    assert p.summary()["sha256"] == sha
    f.write_text(f.read_text() + "\nOne more sentence.\n")
    assert prereg.require("my_rule", tmp_path, committed=False) != sha  # any edit shows in the hash


@pytest.mark.parametrize(("change", "message"), [
    (lambda t: re.sub(r"## Fill model.*?(?=## Pass marks)", "", t, flags=re.S), "fill model"),
    (lambda t: re.sub(r"(## Hypothesis\n\n).*?(?=\n## )", r"\1", t, flags=re.S), "hypothesis"),
    (lambda t: t.replace("- **Rule:** `my_rule`", "- **Rule:** `another_rule`"), "'Rule' line"),
    (lambda t: t.replace("- **Status:** pre-registered", "- **Status:** draft"), "'Status' must be"),
    (lambda t: t.replace("- **Date:** 2026-11-12", "- **Date:** next week"), "'Date' must"),
])
def test_an_incomplete_file_is_refused_with_the_reason(tmp_path: Path, change, message):
    put(tmp_path, "my_rule", change(filled()))
    with pytest.raises(PreregError, match=message):
        prereg.require("my_rule", tmp_path, committed=False)


def test_at_most_five_neighbours(tmp_path: Path):
    put(tmp_path, "my_rule", filled(neighbours=5))
    assert len(prereg.load("my_rule", tmp_path, committed=False).neighbours) == 5
    put(tmp_path, "my_rule", filled(neighbours=6))
    with pytest.raises(PreregError, match="6 neighbours; at most 5"):
        prereg.require("my_rule", tmp_path, committed=False)
    put(tmp_path, "my_rule", filled(neighbours=0))
    assert prereg.load("my_rule", tmp_path, committed=False).neighbours == ()


def test_a_retroactive_file_is_accepted_and_marked(tmp_path: Path):
    put(tmp_path, "my_rule", filled(neighbours=8, status="retroactive, first look, cannot pass Gate B"))
    p = prereg.load("my_rule", tmp_path, committed=False)  # the run can be logged with a hash ...
    assert p.retroactive and len(p.neighbours) == 8  # ... and gate v2 will never let it pass Gate B


@pytest.mark.parametrize("name", ["", "../secrets", "Trend", "trend-highs", "a/b", "TEMPLATE"])
def test_only_rule_names_are_looked_up(tmp_path: Path, name: str):
    with pytest.raises(PreregError, match="not a rule name"):
        prereg.require(name, tmp_path, committed=False)


# ---------------------------------------------------------------- committed


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_file_must_be_committed_exactly_as_it_is(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))  # never find a repository above tmp_path
    f = put(tmp_path, "my_rule", filled())
    with pytest.raises(PreregError, match="not a git checkout"):
        prereg.require("my_rule", tmp_path)
    git(tmp_path, "init", "-q")
    with pytest.raises(PreregError, match="is not committed"):
        prereg.require("my_rule", tmp_path)
    git(tmp_path, "add", "docs/prereg/my_rule.md")
    with pytest.raises(PreregError, match="not committed"):  # staged is not committed
        prereg.require("my_rule", tmp_path)
    git(tmp_path, "commit", "-q", "-m", "pre-register my_rule")
    sha = prereg.require("my_rule", tmp_path)
    p = prereg.load("my_rule", tmp_path)
    assert sha == hashlib.sha256(f.read_bytes()).hexdigest() and p.committed_at is not None
    assert p.committed_at.tzinfo is not None and p.summary()["committed_at"].startswith("20")
    f.write_text(f.read_text().replace("Filled in for the test.", "Changed after the run.", 1))
    with pytest.raises(PreregError, match="changes that are not committed"):
        prereg.require("my_rule", tmp_path)


# ---------------------------------------------------------------- the files in the repository


@pytest.mark.parametrize("rule", FIRST_LOOK)
def test_first_look_rules_have_a_retroactive_pre_registration(rule: str):
    p = prereg.load(rule, ROOT, committed=False)
    text = p.path.read_text()
    assert p.retroactive and p.status == "retroactive"
    assert "Retroactive, first look, cannot pass Gate B" in text and "#113" in text
    assert "cannot pass" in p.sections["pass_marks"] and "gate_v2.yaml" in p.sections["pass_marks"]
    assert all(n.startswith(rule + "_") for n in p.neighbours)  # the other settings of the #113 grid
    assert prereg.require(rule, ROOT, committed=False) == hashlib.sha256(p.path.read_bytes()).hexdigest()


def test_the_template_names_every_section_the_checker_wants():
    text = TEMPLATE.read_text().lower()
    for start in prereg.SECTIONS.values():
        assert f"## {start}" in text
    assert "- **status:** pre-registered" in text and "<<rule_name>>" in text
    assert {"TEMPLATE", *FIRST_LOOK} <= {p.stem for p in (ROOT / "docs" / "prereg").glob("*.md")}


# ---------------------------------------------------------------- the long numbered layout (#127)

LONG = """# Pre-registration: `my_rule` (a rule, rule 9 of decision 15)

- **Date:** 12 November 2026. Committed before any code that runs this rule (issue #127).
- **Status of every result from this document:** PROVISIONAL.

## 1. Hypothesis, and who is on the other side

Winners keep winning for some weeks.

The other side of the trade: investors who sell winners too early.

Sources: `docs/research/engine_v3/report.md` section 2.

## 2. Frozen construction (not tuned; published settings)

| Part | Setting |
|---|---|
| Score | 90-session slope |

### Choices where the published rule is silent

1. Weekly on Wednesday.

## 3. Neighbours for the plateau check (declared now; each must pass mark 1)

| Neighbour | Change from the frozen rule |
|---|---|
{rows}

## 4. Fill model

Close of T+1, half a spread a side.

## 7. Pass marks (Gate B of decision 15, section 7, copied)

All of the marks of config/gate_v2.yaml.
"""


def long_layout(n: int = 5) -> str:
    return LONG.replace("{rows}", "\n".join(f"| `nb{i}` | one step |" for i in range(n)))


def test_the_long_numbered_layout_is_accepted(tmp_path: Path):
    put(tmp_path, "my_rule", long_layout())
    p = prereg.load("my_rule", tmp_path, committed=False)
    assert (p.status, p.date.isoformat()) == ("pre-registered", "2026-11-12")
    assert p.neighbours == ("nb0", "nb1", "nb2", "nb3", "nb4")  # the table, without its header
    assert set(p.sections) == set(prereg.SECTIONS)
    assert "docs/research/" in p.sections["evidence"] and "Wednesday" in p.sections["settings"]


@pytest.mark.parametrize(("change", "message"), [
    (lambda t: t.replace("`my_rule`", "`other_rule`", 1), "'Rule' line"),
    (lambda t: t.replace("Sources: `docs/research/engine_v3/report.md` section 2.", ""), "published evidence"),
    (lambda t: t.replace("## 4. Fill model", "## 4. Orders"), "fill model"),
    (lambda t: t.replace("12 November 2026", "mid-November"), "'Date' must"),
])
def test_the_long_layout_is_checked_as_strictly(tmp_path: Path, change, message):
    put(tmp_path, "my_rule", change(long_layout()))
    with pytest.raises(PreregError, match=message):
        prereg.require("my_rule", tmp_path, committed=False)


def test_a_neighbour_table_counts_towards_the_five(tmp_path: Path):
    put(tmp_path, "my_rule", long_layout(6))
    with pytest.raises(PreregError, match="6 neighbours; at most 5"):
        prereg.require("my_rule", tmp_path, committed=False)


@pytest.mark.parametrize("rule", sorted(p.stem for p in (ROOT / "docs" / "prereg").glob("*.md")
                                        if p.stem != "TEMPLATE"))
def test_every_committed_pre_registration_is_readable(rule: str):
    p = prereg.load(rule, ROOT, committed=False)
    assert p.rule == rule and len(p.sha256) == 64 and set(p.sections) == set(prereg.SECTIONS)
    if not p.retroactive:
        assert len(p.neighbours) <= prereg.MAX_NEIGHBOURS
