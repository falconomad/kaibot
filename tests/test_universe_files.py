"""Every ticker in the universe files must load as text.

YAML reads a bare ON, NO, YES or OFF as true/false, so ON Semiconductor's ticker has to be quoted.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = ["config/universe_history.yaml", "config/universe_seed.yaml", "config/test_group_candidates.yaml"]


def _leaves(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _leaves(value)
    else:
        yield node


@pytest.mark.parametrize("name", FILES)
def test_no_ticker_is_read_as_a_boolean(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    bools = [leaf for leaf in _leaves(yaml.safe_load(path.read_text())) if isinstance(leaf, bool)]
    assert not bools, f"{name}: quote tickers that YAML reads as true/false (e.g. \"ON\")"


def test_on_semiconductor_is_in_the_history_universe():
    cfg = yaml.safe_load((ROOT / "config/universe_history.yaml").read_text())
    assert "ON" in cfg["sectors"]["XLK"]
