"""M12 ticket 3.7 (#469): every drill names the documented outcome it checks, and that sentence is still in its
document. Runs on every pull request (no drill is played here: the `drills` workflow plays them weekly, and
`pytest tests/drills/check_drills.py` plays them locally). A pull request that changes a promise of docs/ops
fails here until it changes the drill that holds the engine to it."""

from __future__ import annotations

import re
from pathlib import Path

import drills
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize("name", list(drills.DRILLS))
def test_the_drill_quotes_its_documents(name):
    fn, docs, known = drills.DRILLS[name]
    assert callable(fn) and docs
    for path, sentence in docs:
        assert _flat(sentence) in _flat((ROOT / path).read_text()), \
            f"{name}: {path} no longer says {sentence!r}: change the drill with the document"


def test_the_ticket_s_drills_are_all_there():
    want = {"watchdog takeover", "dead-man stop placement", "lease lost", "supabase down at start",
            "supabase down at a restart", "journal damaged at start", "journal damaged while running"}
    assert want <= set(drills.DRILLS)


def test_a_known_failure_names_an_issue():
    for name, (_fn, _docs, known) in drills.DRILLS.items():
        assert known is None or (isinstance(known, int) and known > 0), name
