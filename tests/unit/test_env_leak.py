"""#514 (decision 16 section 15, item 6): a variable a test's `.env` adds through `load_env_file` does not reach the
next test. The two tests run in this order (pytest keeps a file's order); the second fails if the first leaked."""

from __future__ import annotations

import os

from kaibot.engine.config import load_env_file

NAME = "KAIBOT_TEST_LEAK_514"


def test_a_test_loads_an_env_file_that_sets_a_broker_url(tmp_path):
    assert NAME not in os.environ
    f = tmp_path / ".env"
    f.write_text(f"{NAME}=https://paper-api.alpaca.markets\n")
    load_env_file(f)
    assert os.environ[NAME] == "https://paper-api.alpaca.markets"


def test_the_next_test_does_not_see_it():
    assert NAME not in os.environ
