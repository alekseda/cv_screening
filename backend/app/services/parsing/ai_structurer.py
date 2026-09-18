"""
Turns raw resume text (Azerbaijani, Russian, or English - or a mix) into the
fixed structured schema the rest of the system relies on.

Why an AI call instead of regex/rule-based parsing: resume formatting is too
inconsistent across three languages and countless templates for reliable
rule-based extraction (section headers vary by language, date formats vary,
layout varies). A forced tool-use call to Claude guarantees the response is
valid JSON matching this exact schema - it cannot reply with prose.

EFFICIENCY PASS (cost reduction, no accuracy loss):
  1. Model tiering: structuring runs on ANTHROPIC_MODEL_STRUCTURING_DEFAULT (a
     cheaper model) first. If the result looks unreliable - the tool wasn't
     called, or the output is suspiciously empty on substantial input - it
     automatically escalates to ANTHROPIC_MODEL (the stronger/previous
     default) and returns THAT result instead. This escalation tier is the
     success-rate safety net for this change and must not be removed to save
     cost further; see `_needs_escalation` for the exact triggers.
  2. Prompt caching: the system prompt used to have today's date interpolated
     into it on every call, which made it a different string every time and
     defeated Claude's prompt cache entirely. The date now goes in the user
     message instead, so the (identical, larger) system prompt can be marked
     cacheable and billed at the reduced cached rate after the first call.
"""
from datetime import date
from typing import Any

from anthropic import Anthropic

from app.core.config import settings

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise ResumeStructuringError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


class ResumeStructuringError(Exception):
    pass


# This schema is the contract for `Candidate.structured_data`. If it changes,
# bump a schema_version field and re-run /candidates/{id}/reparse against the
# stored raw_text rather than re-uploading files.
RESUME_EXTRACTION_TOOL = {
    "name": "extract_resume_data",
    "description": "Record structured candidate data extracted from raw resume text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "full_name": {"type": ["string", "null"]},
            "contact": {
                "type": "object",
                "properties": {
                    "email": {"type": ["string", "null"]},
                    "phone": {"type": ["string", "null"]},
                    "location": {"type": ["string", "null"], "description": "City/country as written on the resume"},
                },
                "required": ["email", "phone", "location"],
            },
            "work_history": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string"},
                        "title": {"type": "string"},
                        "start_date": {"type": ["string", "null"], "description": "YYYY-MM, or YYYY if month unknown"},
                        "end_date": {"type": ["string", "null"], "description": "YYYY-MM, YYYY, or null if this is the current role"},
                        "is_current": {"type": "boolean"},
                        "description": {"type": ["string", "null"], "description": "Responsibilities/achievements, kept close to the original wording"},
                        "stated_duration_months": {
                            "type": ["integer", "null"],
                            "description": (
                                "If the resume explicitly states how long this role lasted (e.g. '3 ay', "
                                "'6 months', '1 il'), convert it to a whole number of months and record it "
                                "here, regardless of whether start_date/end_date are also given. This "
                                "matters most when start_date/end_date are only year-precision - a stated "
                                "duration is more accurate than what could be computed from bare years. "
                                "Null if no explicit duration is stated for this entry."
                            ),
                        },
                    },
                    "required": [
                        "company", "title", "start_date", "end_date", "is_current", "description",
                        "stated_duration_months",
                    ],
                },
            },
            "education": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "institution": {"type": "string"},
                        "degree": {"type": ["string", "null"], "description": "e.g. bachelor, master, phd, or as stated"},
                        "field_of_study": {"type": ["string", "null"]},
                        "start_date": {"type": ["string", "null"]},
                        "end_date": {"type": ["string", "null"]},
                    },
                    "required": ["institution", "degree", "field_of_study", "start_date", "end_date"],
                },
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Technical and professional skills, tools, and competencies mentioned (not certifications or languages - those are separate fields below)",
            },
            "certificates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "issuer": {"type": ["string", "null"]},
                        "date_obtained": {"type": ["string", "null"], "description": "YYYY-MM or YYYY, if stated"},
                    },
                    "required": ["name", "issuer", "date_obtained"],
                },
                "description": "Professional certifications/qualifications (e.g. PMP, CCNA, driving license class, security clearance) - not academic degrees, those belong in education",
            },
            "languages_spoken": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string"},
                        "proficiency": {
                            "type": ["string", "null"],
                            "description": "As stated on the resume (e.g. native, fluent, intermediate, basic) - null if a language is listed with no level given",
                        },
                        "language_code": {
                            "type": ["string", "null"],
                            "description": (
                                "The ISO 639-1 two-letter code for this language (e.g. 'en' for English, "
                                "'az' for Azerbaijani, 'ru' for Russian, 'tr' for Turkish), regardless of "
                                "what script or spelling the resume uses for the language's name. Null only "
                                "if you genuinely don't recognize the language."
                            ),
                        },
                    },
                    "required": ["language", "proficiency", "language_code"],
                },
                "description": "Spoken/written languages and proficiency levels, distinct from programming/technical skills",
            },
            "volunteer_experience": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "organization": {"type": "string"},
                        "role": {"type": ["string", "null"]},
                        "start_date": {"type": ["string", "null"], "description": "YYYY-MM, or YYYY if month unknown"},
                        "end_date": {"type": ["string", "null"], "description": "YYYY-MM, YYYY, or null if ongoing"},
                        "is_current": {"type": "boolean"},
                        "description": {"type": ["string", "null"]},
                        "stated_duration_months": {
                            "type": ["integer", "null"],
                            "description": (
                                "Same as work_history's stated_duration_months - an explicitly stated "
                                "duration (e.g. '3 ay', '6 months'), converted to whole months. Null if not "
                                "stated."
                            ),
                        },
                    },
                    "required": [
                        "organization", "role", "start_date", "end_date", "is_current", "description",
                        "stated_duration_months",
                    ],
                },
                "description": "Unpaid volunteer/community/nonprofit work - kept separate from paid work_history",
            },
            "date_of_birth": {
                "type": ["string", "null"],
                "description": "As explicitly stated on the resume - YYYY-MM-DD, YYYY-MM, or YYYY if only the year is given. Null if not stated.",
            },
            "age": {
                "type": ["integer", "null"],
                "description": (
                    "The candidate's age. Use the explicitly stated age if given. If no age is stated but "
                    "date_of_birth is, calculate it from date_of_birth using today's date (see below). "
                    "Never infer age from any other signal (photo, graduation year, job start dates, etc.). "
                    "Null if neither an age nor a date_of_birth is stated."
                ),
            },
            "height_cm": {
                "type": ["number", "null"],
                "description": "Height in centimeters, only if explicitly stated on the resume. Null if not stated.",
            },
            "education_level": {
                "type": ["string", "null"],
                "enum": ["high_school", "bachelor", "master", "phd", None],
                "description": "The highest level of education stated on the resume. Null if it cannot be determined.",
            },
            "military_service": {
                "type": "string",
                "enum": ["completed", "exempt", "deferred", "in_progress", "not_stated"],
                "description": (
                    "Military service status - only set to anything other than 'not_stated' if the resume "
                    "explicitly mentions it. Never infer from gender, age, or any other signal."
                ),
            },
            "document_type": {
                "type": "string",
                "enum": ["resume", "not_a_resume", "blank_or_unreadable"],
                "description": (
                    "What this document actually is. 'resume' for a CV/resume (even a sparse or unusual one). "
                    "'not_a_resume' for a document that is clearly something else entirely (national ID, "
                    "passport scan, diploma, cover letter with no CV content, invoice, etc.). "
                    "'blank_or_unreadable' if the text is empty, garbled beyond use, or contains no "
                    "meaningful content. Be conservative: only classify as not_a_resume when it is clearly "
                    "not a CV, not merely because a CV is short or missing sections."
                ),
            },
            "detected_language": {
                "type": "string",
                "enum": ["az", "ru", "en", "mixed", "unknown"],
                "description": "Dominant language of the resume",
            },
            "currently_enrolled": {
                "type": "boolean",
                "description": (
                    "True if the resume indicates the candidate is presently enrolled in a degree program "
                    "and has not yet graduated from it (e.g. 'final-year student', '4th-year student', "
                    "'sonuncu kurs tələbəsi', 'hazırda tələbəyəm'). False otherwise."
                ),
            },
            "current_degree_level": {
                "type": ["string", "null"],
                "enum": ["high_school", "bachelor", "master", "phd", None],
                "description": "If currently_enrolled is true, the degree level currently being pursued. Null if not currently enrolled.",
            },
            "current_year_of_study": {
                "type": ["integer", "null"],
                "description": (
                    "If currently_enrolled is true and the resume states which year of the program the "
                    "candidate is in (e.g. '4th-year student', 'son kurs'), record the numeric year (e.g. 4). "
                    "Null if not stated or not currently enrolled. This is kept alongside "
                    "expected_graduation_year so a recruiter can see why a graduation year was derived, not "
                    "just the bare number."
                ),
            },
            "expected_graduation_year": {
                "type": ["integer", "null"],
                "description": (
                    "If currently_enrolled is true, the year the candidate is expected to graduate. Priority "
                    "order: (1) an explicitly stated expected graduation year; (2) the stated end date of the "
                    "in-progress education entry (resumes often list an ongoing degree as a year range, e.g. "
                    "'2021-2025, davam edir' / '2021-2025, in progress' - use that end year); (3) if neither is "
                    "available but current_year_of_study and a start year are both stated, reason from standard "
                    "program lengths (e.g. a 4-year bachelor's) to estimate it. Null if none of these can be "
                    "determined - do not guess without any basis."
                ),
            },
            "highest_completed_degree_graduation_year": {
                "type": ["integer", "null"],
                "description": "The graduation year of the candidate's highest COMPLETED degree (education_level), if stated. Null if unknown or not applicable.",
            },
        },
        "required": [
            "document_type",
            "full_name",
            "contact",
            "work_history",
            "volunteer_experience",
            "education",
            "skills",
            "certificates",
            "languages_spoken",
            "date_of_birth",
            "age",
            "height_cm",
            "education_level",
            "military_service",
            "detected_language",
            "currently_enrolled",
            "current_degree_level",
            "current_year_of_study",
            "expected_graduation_year",
            "highest_completed_degree_graduation_year",
        ],
    },
}

SYSTEM_PROMPT = """You are a resume-parsing engine for an HR screening tool. Extract information \
EXACTLY as it appears in the resume text - never invent, embellish, or guess facts that aren't \
present. Resumes may be in Azerbaijani, Russian, English, or a mix of these.

Rules:
- document_type: first decide what this document actually is. If it isn't a CV at all (e.g. a national ID \
scan, a diploma, an invoice) mark it not_a_resume; if it's empty or garbled beyond use, mark it \
blank_or_unreadable. In either of those cases still fill the remaining fields with nulls/empty arrays \
rather than inventing content. Be conservative - a short, sparse, or unusually formatted CV is still a resume.
- If a field is genuinely absent, use null (or an empty array where appropriate) - never fabricate.
- age and height_cm are sensitive fields. height_cm: extract ONLY if explicitly stated (e.g. "Height: 175cm") \
- never infer, calculate, or estimate it. age: use the explicitly stated age if given (e.g. "Yaş: 28", \
"Возраст: 28", "Age: 28"); if no age is stated but date_of_birth is, calculate age from date_of_birth using \
today's date (provided below) - this is arithmetic on an explicit fact, not inference. Never derive age or \
height from any other signal (photo, graduation year, job start dates, etc.). Use null when nothing usable \
is stated.
- date_of_birth: record it as stated whenever the resume gives one, even if an explicit age is also given -
this is kept for audit/transparency alongside the age field.
- education_level is the highest level of education stated on the resume (high_school, bachelor, master, \
or phd) - use null only if it genuinely cannot be determined from the education section.
- military_service: only set this to a value other than "not_stated" if the resume explicitly mentions \
military service status. Never infer it from the candidate's apparent gender, age, or any other signal.
- work_history and volunteer_experience dates: extract start_date/end_date exactly as they'd need to be to \
compute an accurate duration later (this matters a lot - a downstream system computes total experience from \
these dates, so precision here is important). You do NOT need to sum durations yourself - just extract each \
entry's dates as accurately as possible.
- stated_duration_months: some resumes state a role's duration directly (e.g. "3 ay", "6 months", "1 il 2 ay") \
even when start_date/end_date are only year-precision. Whenever a duration is stated anywhere near an entry, \
record it here as a whole number of months (convert years to months, e.g. "1 il 2 ay" -> 14) - a stated \
duration is more accurate than what could be computed from bare years, and the downstream system will prefer \
it specifically in that case. Leave this null if no duration is stated for that entry, even if start_date/ \
end_date are precise enough to compute one - it exists to capture information that's ONLY stated as a \
duration, not to duplicate what the dates already say.
- currently_enrolled / current_degree_level / current_year_of_study / expected_graduation_year describe a \
candidate who is still studying and hasn't graduated yet (e.g. "final-year student", "4th-year student", \
"sonuncu kurs tələbəsi"). This is separate from education_level, which is the highest degree already \
COMPLETED - a final-year student with no prior degree would have currently_enrolled=true, \
current_degree_level="bachelor", current_year_of_study=4, and education_level=null or whatever lower degree \
(e.g. high_school) they've actually finished. Getting expected_graduation_year right matters for matching \
against postings that want "final-year student or recent graduate" - see the field's own description for \
how to derive it when it's not stated outright.
- highest_completed_degree_graduation_year is the graduation year for education_level specifically (the \
highest completed degree) - record it whenever the resume states it, regardless of currently_enrolled.
- Keep work_history descriptions close to the candidate's own wording rather than summarizing away \
specifics - a recruiter will read these to judge relevance.
- certificates are professional qualifications (e.g. PMP, CCNA, forklift license, security clearance) - \
keep these separate from academic degrees (which go under education) and from general skills.
- languages_spoken covers spoken/written language proficiency (e.g. Azerbaijani, Russian, English) - \
keep these separate from technical/professional skills. Always fill in language_code with the ISO \
639-1 code for the language itself, independent of how it's spelled/misspelled on the resume (e.g. \
both "İngilis dili" and "İnglis dili" are language_code "en") - this is what downstream matching \
relies on, not the raw spelling.
- volunteer_experience is unpaid community/nonprofit/volunteer work - keep it separate from paid \
work_history even if the resume lists them in the same section.
- Always respond by calling the extract_resume_data tool. Do not respond with plain text."""


def _cached_system_blocks() -> list[dict]:
    """System prompt as a cacheable content block. Static across every call
    now that the date lives in the user message instead (see structure_resume),
    so Claude's prompt cache can actually hit after the first call."""
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _extract_tool_input(response) -> dict[str, Any] | None:
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_resume_data":
            return block.input
    return None


# Escalation triggers - deliberately conservative (biased toward escalating)
# since a missed escalation would silently ship a bad parse, while an
# unnecessary escalation only costs one extra call on an already-cheap path.
_ESCALATION_MIN_TEXT_CHARS = 400  # below this, sparse output is plausibly just a sparse resume


def _needs_escalation(payload: dict[str, Any] | None, truncated_text: str) -> bool:
    if payload is None:
        return True  # the cheap model didn't call the tool at all - schema failure
    long_input = len(truncated_text) > _ESCALATION_MIN_TEXT_CHARS
    looks_empty = (
        not payload.get("full_name")
        and not payload.get("skills")
        and not payload.get("work_history")
        and not payload.get("education")
    )
    if long_input and looks_empty:
        return True
    # document_type is required; a missing value on real input is itself a
    # sign the cheap model's tool call didn't come back well-formed.
    if payload.get("document_type") is None:
        return True
    return False


def _call_model(client: Anthropic, model: str, truncated_text: str, today_str: str) -> dict[str, Any] | None:
    user_content = (
        f"Today's date is {today_str}. Use this as \"present\" when calculating durations for "
        "ongoing roles (is_current=true or end_date=null), and as the reference date when "
        f"calculating age from date_of_birth.\n\nResume text:\n\n{truncated_text}"
    )
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=_cached_system_blocks(),
        tools=[RESUME_EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "extract_resume_data"},
        messages=[{"role": "user", "content": user_content}],
    )
    return _extract_tool_input(response)


def structure_resume(raw_text: str) -> dict[str, Any]:
    client = _get_client()

    text = raw_text.strip()
    if not text:
        raise ResumeStructuringError("No extractable text in resume")

    truncated = text[: settings.MAX_RESUME_TEXT_CHARS]
    today_str = date.today().isoformat()

    try:
        payload = _call_model(client, settings.ANTHROPIC_MODEL_STRUCTURING_DEFAULT, truncated, today_str)
    except Exception as e:
        raise ResumeStructuringError(f"Claude API call failed: {e}") from e

    if _needs_escalation(payload, truncated):
        try:
            escalated = _call_model(client, settings.ANTHROPIC_MODEL, truncated, today_str)
        except Exception as e:
            # The cheap-tier result (if any) is still better than nothing;
            # only fail outright if there's nothing usable at all.
            if payload is not None:
                return payload
            raise ResumeStructuringError(f"Claude API call failed: {e}") from e
        if escalated is not None:
            return escalated
        if payload is not None:
            return payload
        raise ResumeStructuringError("Model response did not include structured data")

    return payload