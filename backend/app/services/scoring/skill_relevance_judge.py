"""
Semantic fallback for requirements the literal token matcher couldn't confirm.

skill_matcher.match_skill_list is deliberately literal (see its own module
docstring): a requirement only counts as matched if enough of its words
appear verbatim in the resume. That's the auditable, cheap, always-on first
pass - but it also produces real false negatives. "Designed dashboards and
visualizations" doesn't literally contain "visualization" (singular), and
"collected, cleaned, and validated large datasets" is data handling even
though the word "handling" never appears anywhere on the resume.

This module is the second pass: for requirements the token matcher marked
missing, ask an LLM whether the resume's ACTUAL content - not just its
wording - demonstrates that skill, and why. Cached per (candidate, vacancy,
requirement) in candidate_skill_relevance (see _skill_relevances_for in
routes/matching.py), so re-scoring after a weight change costs nothing, and
only ever runs for requirements that already failed the free literal check.

Why an LLM rather than embedding cosine similarity: this codebase already
tried embeddings for the structurally identical problem of job-relevance
matching and abandoned it (see relevance_judge.py's own docstring) - cosine
similarity between short multilingual professional phrases sits in a narrow,
low-signal band and tracks text length more than meaning. "Does this resume's
content demonstrate this requirement" is a language-understanding task, the
same category of problem, so it gets the same treatment: a judgement call,
not a vector comparison.
"""
from anthropic import Anthropic

from app.core.config import settings

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise SkillRelevanceError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


class SkillRelevanceError(Exception):
    pass


SKILL_RELEVANCE_TOOL = {
    "name": "rate_skill_relevance",
    "description": (
        "For each requirement, decide whether the candidate's resume content demonstrates it, "
        "even if not stated in the same words."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ratings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement_index": {
                            "type": "integer",
                            "description": "The index number shown next to the requirement",
                        },
                        "matched": {
                            "type": "boolean",
                            "description": "True if the resume's actual content demonstrates this requirement",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One short sentence explaining the decision, for a recruiter to read",
                        },
                    },
                    "required": ["requirement_index", "matched", "reason"],
                },
            }
        },
        "required": ["ratings"],
    },
}

SYSTEM_PROMPT = """You decide whether a candidate's resume content demonstrates specific job requirements, for an \
HR screening tool. A cheap keyword matcher already ran and flagged these requirements as NOT literally worded in \
the resume - your job is to judge the substance, not the wording: is there real evidence in the resume, even if \
phrased completely differently, described only through work actually done, or written in a different language?

Rules:
- Only say matched=true if the resume genuinely demonstrates the requirement. Do not be generous - a vague or \
tangential connection is NOT a match. If the requirement isn't really there, say matched=false.
- A requirement can be demonstrated through skills listed, a job description, a certificate, or education - \
anywhere in the resume content given.
- Plural/singular forms, synonyms, and cross-language wording differences (Azerbaijani/Russian/English) are NOT \
reasons to say no - judge the substance, not the exact words.
- Exception - MS Excel requirements specifically: if the resume shows genuine, real Excel use (reports, \
spreadsheets, data/database processing, tracking, calculations), that's enough evidence for baseline features \
like pivot tables and basic formulas too - these are standard skills almost anyone who actually uses Excel for \
this kind of work has picked up, and CVs describe what the work was for, not which menu commands were used. Only \
say no for these if Excel itself isn't genuinely demonstrated, or the resume's own words make clear the use was \
extremely minimal (e.g. just data entry). Keep every other tool (SQL, Power BI, Tableau, programming languages, \
etc.) to the strict standard above - this exception is for Excel's own baseline features only.
- Always respond by calling the rate_skill_relevance tool, with exactly one rating per requirement listed."""


def _format_requirement(index: int, requirement: str) -> str:
    return f"[{index}] {requirement}"


def judge_skill_relevance(requirements: list[str], resume_text: str) -> dict[str, dict]:
    """Returns {requirement: {"matched": bool, "reason": str}} for every
    requirement in `requirements` the model rated.

    Raises SkillRelevanceError on failure; callers should treat a missing
    entry as still unmatched rather than crediting it by default.
    """
    if not requirements:
        return {}

    client = _get_client()

    text = (resume_text or "").strip() or "(no resume content available)"
    if len(text) > 6000:
        text = text[:6000] + "..."

    req_block = "\n".join(_format_requirement(i, r) for i, r in enumerate(requirements))

    user_content = (
        f"CANDIDATE'S RESUME CONTENT\n{text}\n\n"
        f"REQUIREMENTS FLAGGED AS NOT LITERALLY WORDED IN THE RESUME\n{req_block}\n\n"
        f"Judge all {len(requirements)} requirements."
    )

    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[SKILL_RELEVANCE_TOOL],
            tool_choice={"type": "tool", "name": "rate_skill_relevance"},
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        raise SkillRelevanceError(f"Claude API call failed: {e}") from e

    ratings: list | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "rate_skill_relevance":
            ratings = block.input.get("ratings")
            break

    if ratings is None:
        raise SkillRelevanceError("Model response did not include ratings")

    out: dict[str, dict] = {}
    for rating in ratings:
        try:
            index = int(rating["requirement_index"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < len(requirements):
            out[requirements[index]] = {
                "matched": bool(rating.get("matched")),
                "reason": str(rating.get("reason") or ""),
            }

    return out
