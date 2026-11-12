"""Story S02: the ported bloodbath movers parser reproduces bloodbath's own outputs exactly.

Fixture recorded from bloodbath's code by scripts/make_bloodbath_fixtures.py. The ported indicators
(features/bloodbath.py) and their 307 parity cases were removed in #122: nothing in the trading path called them."""

import json
from pathlib import Path

import pytest

from kaibot.engine.alpaca import parse_movers

FIX = json.loads((Path(__file__).parent.parent / "fixtures" / "bloodbath_parity.json").read_text())


def test_fixture_is_from_bloodbath():
    assert FIX["bloodbath_commit"] and FIX["movers"]


@pytest.mark.parametrize("i", range(len(FIX["movers"])))
def test_movers_parity(i):
    c = FIX["movers"][i]
    assert parse_movers(c["raw"]) == c["expected"]
