"""Agent-room contracts. Agents return structured JSON validated against these models.

Invalid or missing output => no trade (fail closed). Agents may VETO or DOWNGRADE a deterministic
candidate; they never set entry, stop, size or exit (those come from strategies + risk gate).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DropCause(StrEnum):
    COMPANY_NEWS = "company_news"  # 8-K, earnings, guidance, downgrade, litigation -> do NOT buy the dip
    SECTOR = "sector"
    MACRO = "macro"  # rates, CPI, Fed, tariffs, geopolitics
    NOISE = "noise"  # no identifiable cause; liquidity / flows
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceRef(BaseModel):
    id: str = Field(description="deduplicated story/filing id from the evidence store")
    source: str
    published_at: str


class ProposerCase(BaseModel):
    candidate_id: str
    thesis: str = Field(max_length=400)
    cause: DropCause
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)


class CriticCase(BaseModel):
    candidate_id: str
    strongest_counter: str = Field(max_length=400)
    cause: DropCause
    red_flags: list[str] = Field(default_factory=list, max_length=6)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=8)


class JudgeVerdict(BaseModel):
    candidate_id: str
    verdict: str = Field(pattern="^(allow|downgrade|veto)$")
    cause: DropCause
    confidence: Confidence
    invalidation: str = Field(max_length=200, description="observable condition that kills the thesis")
    horizon_minutes: int = Field(ge=5, le=390)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    ttl_seconds: int = Field(default=600, ge=60, le=3600)


def admissible(v: JudgeVerdict, strategy: str) -> bool:
    """Deterministic post-check: e.g. the dip-reversal strategy may never trade a company-news drop."""
    if v.verdict == "veto":
        return False
    return not (strategy == "dip_reversal" and v.cause in (DropCause.COMPANY_NEWS, DropCause.UNKNOWN))


class TriageScore(BaseModel):
    """Haiku's one score per deduplicated news cluster (story S33). No price, size, stop or exit fields:
    triage only labels evidence for the rules-only veto and the room."""

    model_config = ConfigDict(extra="forbid")

    cause: DropCause
    materiality: float = Field(ge=0.0, le=1.0, description="0 = irrelevant to the stock today, 1 = thesis-changing")
    direction: Literal["negative", "neutral", "positive"]
    summary: str = Field(max_length=200)
