"""
Turns a raw, pasted job description (Azerbaijani, Russian, English, or a mix)
into structured vacancy fields - responsibilities, hard vs. preferred
requirements, experience/education thresholds.

Mirrors ai_structurer.py's approach for resumes: a forced tool-use call
guarantees valid structured output instead of trying to regex-parse wildly
inconsistent posting formats and languages.
"""
from typing import Any

from anthropic import Anthropic

from app.core.config import settings

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise JDStructuringError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


class JDStructuringError(Exception):
    pass


JD_EXTRACTION_TOOL = {
    "name": "extract_vacancy_data",
    "description": "Record structured vacancy data extracted from a raw job description.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": ["string", "null"],
                "description": "A short job title/position name for this posting, as stated or clearly implied. Null only if it truly cannot be determined.",
            },
            "responsibilities": {
                "type": ["string", "null"],
                "description": "The responsibilities/duties section of the posting, kept close to the original wording (e.g. preserved as a bullet-style list). Null if there's no distinct responsibilities section.",
            },
            "required_skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Mandatory requirements from the posting - qualifications, skills, experience, or status "
                    "criteria NOT marked as preferred/nice-to-have. Each item should be short and specific "
                    "(e.g. 'MS Excel - Pivot tables and core formulas', 'Azerbaijani - fluent'), not a full sentence."
                ),
            },
            "preferred_skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Nice-to-have/preferred qualifications explicitly marked as such in the posting - e.g. "
                    "words/phrases like 'üstünlükdür' (Azerbaijani for 'is an advantage'), 'является преимуществом', "
                    "'is a plus', 'preferred', 'nice to have', 'bonus'."
                ),
            },
            "min_experience_years": {
                "type": ["number", "null"],
                "description": "Minimum years of professional experience explicitly required, if stated. Null if not stated (e.g. an internship/entry-level posting with no experience requirement).",
            },
            "education_requirement": {
                "type": ["string", "null"],
                "enum": ["none", "high_school", "bachelor", "master", "phd", None],
                "description": (
                    "The degree level associated with this posting's education requirement (e.g. 'bachelor' "
                    "for a bachelor's-level role), even if accepts_current_students is also true for it. "
                    "Use 'none' if there is explicitly no education requirement, or null if it can't be determined."
                ),
            },
            "accepts_current_students": {
                "type": "boolean",
                "description": (
                    "True if the posting explicitly also accepts candidates who are still enrolled and "
                    "haven't completed education_requirement's degree yet (e.g. 'final-year student', "
                    "'last-year student', 'sonuncu kurs tələbəsi', 'currently pursuing'). False if a "
                    "completed degree is strictly required."
                ),
            },
            "graduation_year_min": {
                "type": ["integer", "null"],
                "description": "If the posting specifies an acceptable graduation-year window for candidates who have already graduated (e.g. '2024/2025 graduate'), the earliest acceptable year. Null if not specified.",
            },
            "graduation_year_max": {
                "type": ["integer", "null"],
                "description": "The latest acceptable graduation year from the stated window. Null if not specified or there's no upper bound.",
            },
        },
        "required": [
            "title",
            "responsibilities",
            "required_skills",
            "preferred_skills",
            "min_experience_years",
            "education_requirement",
            "accepts_current_students",
            "graduation_year_min",
            "graduation_year_max",
        ],
    },
}

SYSTEM_PROMPT = """You are a job-description parsing engine for an HR screening tool. Postings may be in \
Azerbaijani, Russian, English, or a mix of these.

Rules:
- required_skills vs preferred_skills: anything explicitly marked as preferred/nice-to-have/a plus/bonus \
goes in preferred_skills (in Azerbaijani, "üstünlükdür" means "is an advantage/preferred"; in Russian, \
"является преимуществом" or "приветствуется" signal the same thing). Everything else under the \
requirements section is a hard requirement.
- Keep each required/preferred item short and specific (a phrase, not a full sentence) so it can be \
matched against a candidate's individual skills later.
- responsibilities should stay close to the original wording/structure (e.g. preserve it as a bullet list) \
rather than being summarized away.
- education_requirement / accepts_current_students / graduation_year_min / graduation_year_max: read \
carefully whether the posting requires a COMPLETED degree (accepts_current_students=false) or explicitly \
also accepts students who haven't graduated yet (accepts_current_students=true) - set education_requirement \
to the degree level either way (the level the role is pitched at), and use accepts_current_students as the \
modifier for whether not-yet-graduated candidates at that level still qualify.
- Never invent requirements that aren't stated or clearly implied by the posting.
- Always respond by calling the extract_vacancy_data tool. Do not respond with plain text."""


def structure_vacancy(raw_description: str) -> dict[str, Any]:
    client = _get_client()

    text = raw_description.strip()
    if not text:
        raise JDStructuringError("No extractable text in job description")

    truncated = text[: settings.MAX_RESUME_TEXT_CHARS]  # JDs are similar length to resumes; same budget is fine

    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[JD_EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "extract_vacancy_data"},
            messages=[{"role": "user", "content": f"Job description:\n\n{truncated}"}],
        )
    except Exception as e:
        raise JDStructuringError(f"Claude API call failed: {e}") from e

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_vacancy_data":
            return block.input

    raise JDStructuringError("Model response did not include structured data")
