"""Regression tests for the efficiency-pass changes (no DB, no API keys).

Covers the two pieces of new pure logic that carry correctness risk:
  1. apply_cached_parse must copy EVERY parsed field, so a candidate whose
     parse is reused across vacancies is indistinguishable from one parsed
     directly - a missed field would silently degrade scoring for reused CVs.
  2. The structuring escalation triggers must fire on unreliable output and
     stay quiet on genuinely-sparse-but-valid output.

Run:  python backend/tests/test_efficiency_pass.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-only-not-a-real-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("VOYAGE_API_KEY", "test")

from app.services.parsing.ai_structurer import _needs_escalation  # noqa: E402
from app.services.parsing.pipeline import apply_cached_parse  # noqa: E402


# The full set of parsed attributes apply_cached_parse is responsible for.
# If a new parsed column is added to Candidate, it must be added here AND to
# apply_cached_parse - this list is the checklist that keeps them in sync.
COPIED_FIELDS = [
    "raw_text", "structured_data", "document_type", "detected_language",
    "total_work_experience_years", "total_work_experience_is_estimate",
    "total_volunteer_experience_years", "total_volunteer_experience_is_estimate",
    "age", "date_of_birth", "height_cm", "education_level",
    "highest_completed_degree_graduation_year", "military_service",
    "currently_enrolled", "current_degree_level", "current_year_of_study",
    "expected_graduation_year", "embedding", "embedding_error",
]


def test_apply_cached_parse_copies_every_field():
    source = SimpleNamespace(
        raw_text="RAW", structured_data={"full_name": "Aysel", "skills": ["Excel"]},
        document_type="resume", detected_language="az",
        total_work_experience_years=3.2, total_work_experience_is_estimate=True,
        total_volunteer_experience_years=0.5, total_volunteer_experience_is_estimate=False,
        age=29, date_of_birth="1996", height_cm=170.0, education_level="bachelor",
        highest_completed_degree_graduation_year=2018, military_service="not_stated",
        currently_enrolled=False, current_degree_level=None, current_year_of_study=None,
        expected_graduation_year=None, embedding=[0.1, 0.2, 0.3], embedding_error=None,
    )
    target = SimpleNamespace(**{f: None for f in COPIED_FIELDS},
                             parsing_status="pending", parsing_error="was pending")

    apply_cached_parse(target, source)

    for f in COPIED_FIELDS:
        assert getattr(target, f) == getattr(source, f), f"field not copied: {f}"
    assert target.parsing_status == "success"
    assert target.parsing_error is None
    print("apply_cached_parse copies every field: OK")


def test_escalation_triggers():
    long_text = "x" * 500
    # tool not called at all -> escalate
    assert _needs_escalation(None, long_text) is True
    # long input, everything empty -> escalate
    assert _needs_escalation(
        {"document_type": "resume", "full_name": None, "skills": [],
         "work_history": [], "education": []}, long_text) is True
    # missing document_type on substantive output -> escalate
    assert _needs_escalation(
        {"document_type": None, "full_name": "A", "skills": ["x"],
         "work_history": [], "education": []}, long_text) is True
    # genuinely short & sparse resume -> do NOT escalate
    assert _needs_escalation(
        {"document_type": "resume", "full_name": None, "skills": [],
         "work_history": [], "education": []}, "short") is False
    # long input with real data -> do NOT escalate
    assert _needs_escalation(
        {"document_type": "resume", "full_name": "Aysel", "skills": ["Excel"],
         "work_history": [{"title": "Analyst"}], "education": []}, long_text) is False
    print("escalation triggers: OK")


if __name__ == "__main__":
    test_apply_cached_parse_copies_every_field()
    test_escalation_triggers()
    print("\nALL EFFICIENCY-PASS TESTS PASSED")
