"""Ad-hoc end-to-end check of the scoring engine (no DB, no API keys needed).

Run from anywhere:  python backend/tests/test_scoring_scenarios.py
"""
import os
import sys
from types import SimpleNamespace

# Make `app` importable regardless of the working directory this is run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The engine imports settings, which requires a JWT secret to be present.
os.environ.setdefault("JWT_SECRET_KEY", "test-only-not-a-real-secret")

from app.models.vacancy import DEFAULT_SCORING_WEIGHTS  # noqa: E402
from app.services.scoring.engine import score_candidate  # noqa: E402


def make_vacancy(**overrides):
    base = dict(
        title="HR Data Analytics Intern",
        description="PASHA Property Management internship in HR data analytics.",
        responsibilities="Collect and update HR data; prepare tables and reports in MS Excel.",
        required_skills=[
            "MS Excel üzrə yaxşı səviyyədə biliklər (Pivot, əsas formulalar)",
            "Azərbaycan dili - sərbəst",
        ],
        required_skills_canonical=[
            "Microsoft Excel proficiency (Pivot tables, core formulas)",
            "Azerbaijani language - fluent",
        ],
        required_skills_type=["hard", "hard"],
        required_skills_language_code=[None, "az"],
        preferred_skills=["Power BI/Tableau", "SQL"],
        preferred_skills_canonical=["Power BI/Tableau", "SQL"],
        preferred_skills_type=["hard", "hard"],
        preferred_skills_language_code=[None, None],
        min_experience_years=1.0,
        education_requirement="bachelor",
        accepts_current_students=True,
        graduation_year_min=2024,
        graduation_year_max=2025,
        wage_min=500.0,
        wage_max=800.0,
        wage_currency="AZN",
        scoring_weights=dict(DEFAULT_SCORING_WEIGHTS),
        embedding=[0.1] * 1024,
        responsibilities_embedding=[0.1] * 1024,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_candidate(**overrides):
    structured = overrides.pop("structured_data", None) or {
        "document_type": "resume",
        "skills": ["MS Excel", "Pivot Tables", "SQL"],
        "skills_canonical": ["Microsoft Excel", "Pivot Tables", "SQL"],
        "certificates": [],
        "languages_spoken": [{"language": "Azərbaycan dili", "proficiency": "sərbəst"}],
        "work_history": [
            {
                "title": "HR Intern",
                "company": "Acme",
                "start_date": "2024-01",
                "end_date": "2025-06",
                "is_current": False,
                "description": "Collected HR data and built Excel reports",
            }
        ],
        "volunteer_experience": [],
        "education": [
            {"institution": "UNEC", "degree": "bachelor", "field_of_study": "Economics",
             "start_date": "2021-09", "end_date": "2025-06"}
        ],
    }
    base = dict(
        original_filename="cv.pdf",
        parsing_status="success",
        parsing_error=None,
        structured_data=structured,
        document_type=structured.get("document_type"),
        education_level="bachelor",
        currently_enrolled=False,
        current_degree_level=None,
        expected_graduation_year=None,
        highest_completed_degree_graduation_year=2025,
        total_work_experience_years=1.4,
        wage_expectation=700.0,
        wage_currency="AZN",
        embedding=[0.1] * 1024,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def show(label, result):
    b = result["breakdown"]
    print(f"\n=== {label} ===")
    print(f"TOTAL {result['total_score']} (rule-based {result['hard_filter_score']} / responsibilities {result['semantic_score']})")
    if not b.get("scorable"):
        print("  NOT SCORABLE:", b["reason"])
        return
    for name, comp in b["components"].items():
        print(f"  {name:18s} score={comp['score']:<6} weight={comp['weight']:<6} contributes {comp['contribution']}")
    print("  required matched:", b["required_skills"]["matched"])
    print("  required missing:", b["required_skills"]["missing"])
    print("  experience:", b["experience"]["reason"])
    print("  education:", b["education"]["passed"], "-", b["education"]["reason"])
    print("  wage:", b["wage"]["status"], "-", b["wage"]["reason"])


# 1. Strong candidate: 2025 bachelor's grad, Excel skills, in budget
show("Strong match (2025 grad)", score_candidate(make_candidate(), make_vacancy(), {0: {'relevance': 1.0, 'reason': 'Same role'}}))

# 2. Master's holder from years ago - should FAIL education per the agreed rule
show(
    "Master's from 2019 (should fail education)",
    score_candidate(
        make_candidate(
            education_level="master",
            highest_completed_degree_graduation_year=2019,
            structured_data={
                "document_type": "resume",
                "skills": ["MS Excel", "Pivot Tables"],
                "certificates": [], "languages_spoken": [{"language": "Azərbaycan dili", "proficiency": "sərbəst"}],
                "work_history": [], "volunteer_experience": [],
                "education": [{"institution": "X", "degree": "master", "field_of_study": "Econ",
                               "start_date": "2017-09", "end_date": "2019-06"}],
            },
        ),
        make_vacancy(),
        {},
    ),
)

# 3. Final-year student, not yet graduated - should PASS education
show(
    "Final-year student (should pass education)",
    score_candidate(
        make_candidate(
            education_level=None,
            currently_enrolled=True,
            current_degree_level="bachelor",
            expected_graduation_year=2025,
            highest_completed_degree_graduation_year=None,
        ),
        make_vacancy(),
        {},
    ),
)

# 4. Non-CV upload - should short-circuit to 0
show(
    "National ID upload (should be 0)",
    score_candidate(
        make_candidate(document_type="not_a_resume"),
        make_vacancy(),
        {},
    ),
)

# 5. Over budget
show(
    "Wage far over budget",
    score_candidate(make_candidate(wage_expectation=1600.0), make_vacancy(), {}),
)

# 6. Missing embeddings -> semantic unavailable, falls back to hard filter only
show(
    "No embeddings (semantic unavailable)",
    score_candidate(make_candidate(embedding=None), make_vacancy(embedding=None), {}),
)

# 7. Required soft skill (e.g. "team player") should be excluded from scoring,
# not counted as missing just because the resume never says "team player".
result = score_candidate(
    make_candidate(),
    make_vacancy(
        required_skills=[
            "MS Excel üzrə yaxşı səviyyədə biliklər (Pivot, əsas formulalar)",
            "Azərbaycan dili - sərbəst",
            "Strong team player",
        ],
        required_skills_canonical=[
            "Microsoft Excel proficiency (Pivot tables, core formulas)",
            "Azerbaijani language - fluent",
            "Strong team player",
        ],
        required_skills_type=["hard", "hard", "soft"],
    ),
    {0: {"relevance": 1.0, "reason": "Same role"}},
)
show("Required soft skill excluded from scoring", result)
not_scored = result["breakdown"]["required_skills"]["not_scored_here"]
assert any(
    e["requirement"] == "Strong team player" and e["reason"] == "Soft skill - excluded from scoring"
    for e in not_scored
), f"Expected soft skill to be excluded with the right reason, got: {not_scored}"
assert "Strong team player" not in result["breakdown"]["required_skills"]["missing"], (
    "Soft skill should never appear as a missing (i.e. scored-and-failed) requirement"
)
print("  OK: soft skill excluded, not penalized")

# 8. Candidate never lists Azerbaijani (common - it's the default working
# language here, so candidates often only list what they consider notable).
# Requiring it should NOT cost points.
result = score_candidate(
    make_candidate(
        structured_data={
            "document_type": "resume",
            "skills": ["MS Excel", "Pivot Tables", "SQL"],
            "certificates": [],
            "languages_spoken": [{"language": "English", "proficiency": "B2"}],  # no Azerbaijani listed
            "work_history": [
                {
                    "title": "HR Intern", "company": "Acme", "start_date": "2024-01",
                    "end_date": "2025-06", "is_current": False,
                    "description": "Collected HR data and built Excel reports",
                }
            ],
            "volunteer_experience": [],
            "education": [
                {"institution": "UNEC", "degree": "bachelor", "field_of_study": "Economics",
                 "start_date": "2021-09", "end_date": "2025-06"}
            ],
        },
    ),
    make_vacancy(),
    {0: {"relevance": 1.0, "reason": "Same role"}},
)
show("Azerbaijani not listed on CV (should still pass)", result)
req_matched = result["breakdown"]["required_skills"]["matched"]
req_missing = result["breakdown"]["required_skills"]["missing"]
assert "Azərbaycan dili - sərbəst" in req_matched, (
    f"Unlisted Azerbaijani should be assumed satisfied, not missing. matched={req_matched} missing={req_missing}"
)
print("  OK: unlisted Azerbaijani assumed satisfied, not penalized")

# 9. AI-resolved language_code should match a requirement/CV spelling that
# isn't in language_matcher's hardcoded alias list at all - proving the
# AI-resolved path works independent of that fallback list.
result = score_candidate(
    make_candidate(
        structured_data={
            "document_type": "resume",
            "skills": ["MS Excel", "Pivot Tables", "SQL"],
            "certificates": [],
            # A spelling/script the alias list has never seen, but tagged
            # with the AI-resolved code exactly as ai_structurer.py would.
            "languages_spoken": [
                {"language": "Anglais (fictional spelling)", "proficiency": "B2", "language_code": "en"},
                {"language": "Azərbaycan dili", "proficiency": "sərbəst", "language_code": "az"},
            ],
            "work_history": [
                {
                    "title": "HR Intern", "company": "Acme", "start_date": "2024-01",
                    "end_date": "2025-06", "is_current": False,
                    "description": "Collected HR data and built Excel reports",
                }
            ],
            "volunteer_experience": [],
            "education": [
                {"institution": "UNEC", "degree": "bachelor", "field_of_study": "Economics",
                 "start_date": "2021-09", "end_date": "2025-06"}
            ],
        },
    ),
    make_vacancy(
        required_skills=[
            "MS Excel üzrə yaxşı səviyyədə biliklər (Pivot, əsas formulalar)",
            "Azərbaycan dili - sərbəst",
            "Requires fluent Frobnitz-glish",  # a made-up phrase the alias list can't parse either
        ],
        required_skills_canonical=[
            "Microsoft Excel proficiency (Pivot tables, core formulas)",
            "Azerbaijani language - fluent",
            "Fluent English",
        ],
        required_skills_type=["hard", "hard", "hard"],
        # AI resolved the made-up 3rd phrase to English, same as it resolved
        # the candidate's made-up "Anglais (fictional spelling)" entry above.
        required_skills_language_code=[None, "az", "en"],
    ),
    {0: {"relevance": 1.0, "reason": "Same role"}},
)
show("AI-resolved language_code bypasses the alias list", result)
req_matched = result["breakdown"]["required_skills"]["matched"]
assert "Requires fluent Frobnitz-glish" in req_matched, (
    f"AI-resolved language_code should match regardless of alias-list coverage, got matched={req_matched}"
)
print("  OK: AI-resolved language_code matched a spelling the alias list has never seen")

# 10. A CV that just lists "University Name, 2017-2021" with no degree word
# anywhere (education_level unset, degree=null on the entry) should still be
# recognized as a completed bachelor's - this is the normal way AZ/RU/EN CVs
# list a degree, not a missing credential.
result = score_candidate(
    make_candidate(
        education_level=None,
        highest_completed_degree_graduation_year=None,
        structured_data={
            "document_type": "resume",
            "skills": ["MS Excel", "Pivot Tables", "SQL"],
            "certificates": [],
            "languages_spoken": [{"language": "Azərbaycan dili", "proficiency": "sərbəst", "language_code": "az"}],
            "work_history": [
                {
                    "title": "Helpdesk", "company": "Bakubus MMC", "start_date": "2024-04",
                    "end_date": None, "is_current": True,
                    "description": "Texniki dəstək göstərilməsi",
                }
            ],
            "volunteer_experience": [],
            "education": [
                {"institution": "Azerbaijan Technical University", "degree": None,
                 "field_of_study": None, "start_date": "2017", "end_date": "2021"},
            ],
        },
    ),
    make_vacancy(accepts_current_students=False, graduation_year_min=None, graduation_year_max=None),
    {0: {"relevance": 1.0, "reason": "Same role"}},
)
show("University-only education entry, no degree word stated (should pass as bachelor)", result)
edu = result["breakdown"]["education"]
assert edu["passed"], f"Expected untitled university education to be inferred as bachelor, got: {edu}"
assert edu["candidate_education_level"] == "bachelor", f"Expected inferred level 'bachelor', got: {edu}"
assert "inferred" in edu["reason"], f"Expected the inference to be flagged in the reason, got: {edu['reason']!r}"
print("  OK: untitled university entry inferred as bachelor and flagged as inferred")
