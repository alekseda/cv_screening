import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.candidate import CandidateSummary


class MatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    vacancy_id: uuid.UUID
    total_score: float
    hard_filter_score: float
    semantic_score: float
    score_breakdown: dict
    created_at: datetime


class RankedCandidate(BaseModel):
    """A ranked result row: the candidate, their score, and the full
    breakdown behind it. The breakdown always travels with the score - the
    whole point is that a recruiter never sees a bare number."""

    candidate: CandidateSummary
    total_score: float
    hard_filter_score: float
    semantic_score: float
    score_breakdown: dict
    scored_at: datetime


class ScoringRunResult(BaseModel):
    """Summary returned after (re)scoring a whole vacancy."""

    vacancy_id: uuid.UUID
    scored: int
    skipped: int
    skipped_reasons: list[str] = []
