"""The evaluation-case format, version 1. One JSON object per line (JSONL):

    {"format_version": 1,
     "case_id": "syn-001",                      unique in the file; also the room's candidate id
     "synthetic": true,                         true = invented for testing; never a real stock, story or outcome
     "category": "clear_veto",                  free text, used to group the report
     "decision_ts": "2026-09-14T15:12:00Z",     when the rules approved the signal (UTC)
     "signal": {"symbol": "...", "strategy": "dip_reversal", "sector_etf": "...", "features": {...}},
     "evidence": {"items": [...], "context": {...}},   the evidence pack exactly as the room receives it
     "outcome": {"label": "win" | "loss", "r_multiple": -1.0, "source": "..."},
     "expected": {"verdict": "veto", "rule": "..."}}   optional: what the room's rules require (synthetic only)

`outcome` is what the rules-only arm (the same trade at full size) went on to do; it is known only after the
decision and is never shown to the room. A change to the meaning of a field bumps FORMAT_VERSION and the file name.

Training cutoff: a real case is only a fair test if the model cannot have seen how it ended. `after_cutoff` keeps
real cases whose decision date is strictly after the cutoff date (plus an optional margin) and all synthetic
cases, which are invented and so cannot have been memorised.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORMAT_VERSION = 1
SYNTHETIC_SET = Path(__file__).with_name("synthetic_v1.jsonl")


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    strategy: str = "dip_reversal"
    sector_etf: str | None = None
    features: dict = Field(default_factory=dict)


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal["win", "loss"]  # of the full-size rules-only trade
    r_multiple: float | None = None
    source: str = "synthetic"


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["allow", "downgrade", "veto"]
    rule: str = ""


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int
    case_id: str = Field(min_length=1)
    synthetic: bool
    category: str
    decision_ts: datetime
    signal: Signal
    evidence: dict
    outcome: Outcome
    expected: Expected | None = None

    @field_validator("format_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != FORMAT_VERSION:
            raise ValueError(f"format_version {v} is not supported (this code reads version {FORMAT_VERSION})")
        return v

    @field_validator("decision_ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return (v.replace(tzinfo=UTC) if v.tzinfo is None else v).astimezone(UTC)

    @property
    def decision_date(self) -> date:
        return self.decision_ts.date()

    def candidate(self) -> dict:
        """The candidate facts as engine/roomflow.py hands them to the room."""
        return {"symbol": self.signal.symbol, "sector_etf": self.signal.sector_etf, **self.signal.features}


def load_cases(path: Path | str = SYNTHETIC_SET) -> list[EvalCase]:
    cases, seen = [], set()
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            c = EvalCase.model_validate(json.loads(line))
        except ValueError as e:
            raise ValueError(f"{path}:{n}: {e}") from None
        if c.case_id in seen:
            raise ValueError(f"{path}:{n}: duplicate case_id {c.case_id}")
        seen.add(c.case_id)
        cases.append(c)
    return cases


def dump_cases(cases: list[EvalCase], path: Path | str) -> None:
    Path(path).write_text("".join(json.dumps(c.model_dump(mode="json", exclude_none=True)) + "\n" for c in cases))


def after_cutoff(cases: list[EvalCase], cutoff: date, margin_days: int = 0) -> tuple[list[EvalCase], list[EvalCase]]:
    """(kept, dropped). A real case is kept only if it was decided strictly after cutoff + margin_days; synthetic
    cases are always kept."""
    limit = cutoff + timedelta(days=margin_days)
    kept = [c for c in cases if c.synthetic or c.decision_date > limit]
    ids = {c.case_id for c in kept}
    return kept, [c for c in cases if c.case_id not in ids]
