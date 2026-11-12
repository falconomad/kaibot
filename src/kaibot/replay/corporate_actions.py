"""Research only (S77, #117): the daily store's corporate-actions table as the engine's corporate-actions hook.

The live engine never reads the daily store (tests/unit/test_daily_store.py); the research command passes this hook
to the engine, which books the splits and dividends of the symbols it holds at an open (engine/ledger.py)."""

from __future__ import annotations

from pathlib import Path

from kaibot.engine.ledger import actions_from_daily_store
from kaibot.ingest.daily_store import DailyStore


def daily_store_actions(root: Path):
    """(session date) -> [Action] for that ex-date, from `<root>/corporate_actions.parquet`."""
    table = DailyStore(root).actions()
    return lambda day: actions_from_daily_store(table, str(day))
