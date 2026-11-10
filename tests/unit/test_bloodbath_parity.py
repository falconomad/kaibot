"""Story S02: the ported bloodbath indicators and movers parser reproduce bloodbath's own outputs exactly.

Fixture recorded from bloodbath's code by scripts/make_bloodbath_fixtures.py (300+ random and edge cases)."""

import json
from pathlib import Path

import pytest

from kaibot.engine.alpaca import parse_movers
from kaibot.features.bloodbath import daily_features, market_regime, technical_score

FIX = json.loads((Path(__file__).parent.parent / "fixtures" / "bloodbath_parity.json").read_text())


def test_fixture_is_substantial():
    assert FIX["bloodbath_commit"] and len(FIX["indicators"]) >= 300


@pytest.mark.parametrize("i", range(len(FIX["indicators"])))
def test_indicator_parity(i):
    c = FIX["indicators"][i]
    assert technical_score("X", c["history"], c["spy"]) == c["technical"]  # exact, incl. the rationale text
    assert market_regime(c["spy"] or []) == c["regime"]
    assert daily_features("X", c["history"], c["spy"] or [], c.get("news")) == c["features"]  # exact floats


@pytest.mark.parametrize("i", range(len(FIX["movers"])))
def test_movers_parity(i):
    c = FIX["movers"][i]
    assert parse_movers(c["raw"]) == c["expected"]
