import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EducationLevel(str, Enum):
    """Ordered low-to-high: none < high_school < bachelor < master < phd.
    Phase 4's hard filter will use this ordering so e.g. a candidate with a
    master's degree satisfies a bachelor's-level requirement."""

    none = "none"
    high_school = "high_school"
    bachelor = "bachelor"
    master = "master"
    phd = "phd"


class ScoringWeights(BaseModel):
    """Configurable per-vacancy weights. All six components are scored 0-1 and
    combined as a flat weighted sum, so they must add up to 1.0 - validated
    here so a typo can't silently skew scoring."""

    responsibilities_weight: float = 0.45
    wage_weight: float = 0.15
    experience_weight: float = 0.15
    required_skills_weight: float = 0.10
    education_weight: float = 0.10
    preferred_skills_weight: float = 0.05

    @model_validator(mode="after")
    def _check_sums(self) -> "ScoringWeights":
        total = (
            self.responsibilities_weight
            + self.wage_weight
            + self.experience_weight
            + self.required_skills_weight
            + self.education_weight
            + self.preferred_skills_weight
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                "All six scoring weights (responsibilities, wage, experience, required_skills, "
                f"education, preferred_skills) must sum to 1.0 (got {round(total, 4)})"
            )
        return self


class VacancyCreate(BaseModel):
    """Only `description` is required - paste the full raw job posting text.
    Everything else is optional: leave it out and it gets auto-extracted from
    the description (title, responsibilities, required/preferred skills,
    min_experience_years, education_requirement, and the enrollment-status
    fields); provide it explicitly to override the AI extraction for that field."""

    description: str = Field(min_length=1, description="Full, raw job description text")
    title: str | None = None
    responsibilities: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    min_experience_years: float | None = None
    education_requirement: EducationLevel | None = None
    accepts_current_students: bool | None = None
    graduation_year_min: int | None = None
    graduation_year_max: int | None = None
    wage_min: float | None = None
    wage_max: float | None = None
    wage_currency: str = "AZN"
    scoring_weights: ScoringWeights | None = None  # None -> DEFAULT_SCORING_WEIGHTS is used


class VacancyUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    responsibilities: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    min_experience_years: float | None = None
    education_requirement: EducationLevel | None = None
    accepts_current_students: bool | None = None
    graduation_year_min: int | None = None
    graduation_year_max: int | None = None
    wage_min: float | None = None
    wage_max: float | None = None
    wage_currency: str | None = None
    scoring_weights: ScoringWeights | None = None


class VacancyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    responsibilities: str | None
    required_skills: list[str]
    preferred_skills: list[str]
    required_skills_canonical: list[str]  # auto-derived, read-only - see canonicalizer.py
    preferred_skills_canonical: list[str]
    min_experience_years: float
    education_requirement: EducationLevel
    accepts_current_students: bool
    graduation_year_min: int | None
    graduation_year_max: int | None
    wage_min: float | None
    wage_max: float | None
    wage_currency: str
    scoring_weights: dict
    embedding_error: str | None  # None + has embedding = ready for semantic matching
    extraction_error: str | None  # set if JD auto-extraction failed - check fields before trusting them
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
