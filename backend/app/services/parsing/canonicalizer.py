"""
Produces an English canonical form for each of a list of short skill/
requirement phrases, so the (deliberately non-semantic, rule-based) skill
matcher can match across languages - "vizuallaşdırma" and "Data visualization"
share no tokens as written, but their canonical forms both become
"data visualization".

Used for BOTH sides of a match: a vacancy's required_skills/preferred_skills
(whichever way they entered the system - AI-extracted or manually typed) and
a candidate's skills. Kept as one shared function rather than building
canonicalization into the JD/resume extraction schemas separately, so there's
exactly one code path to get right and both manually-entered and AI-extracted
lists are treated identically.

Failure here is non-fatal by design: matching still works on the original
text (just without the cross-language benefit), so a canonicalization outage
should never block vacancy creation or resume parsing.
"""
from typing import Any

from anthropic import Anthropic

from app.core.config import settings

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise CanonicalizationError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


class CanonicalizationError(Exception):
    pass


CANONICALIZE_TOOL = {
    "name": "canonicalize_phrases",
    "description": "Record an English canonical form for each input phrase, in the same order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "canonical_forms": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "One English canonical form per input phrase, in the exact same order and count as "
                    "the input. E.g. input 'vizuallaşdırma' -> 'data visualization'; input 'MS Excel' -> "
                    "'Microsoft Excel'."
                ),
            }
        },
        "required": ["canonical_forms"],
    },
}

SYSTEM_PROMPT = """You normalize short skill/requirement phrases (which may be in Azerbaijani, Russian, \
English, or a mix) into clean, standard English canonical forms for text matching.

Rules:
- Translate non-English phrases to English. If a phrase is already in English, still normalize it to a
common standard form (e.g. "excel" -> "Microsoft Excel").
- Keep each canonical form short - a few words, not a sentence - and use the most common/standard English
term for the underlying skill or concept (e.g. "vizuallaşdırma" -> "data visualization", not a literal
word-for-word translation if that wouldn't read naturally).
- Preserve the EXACT order and count of the input: output exactly one canonical form per input phrase.
- Always respond by calling the canonicalize_phrases tool."""


def canonicalize_phrases(phrases: list[str]) -> list[str]:
    """Returns canonical forms in the same order/count as `phrases`.

    Callers should treat a raised CanonicalizationError as "skip it" - fall
    back to using the original phrases as their own canonical form - rather
    than failing whatever operation triggered this.
    """
    if not phrases:
        return []

    client = _get_client()
    numbered = "\n".join(f"{i + 1}. {phrase}" for i, phrase in enumerate(phrases))

    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=[CANONICALIZE_TOOL],
            tool_choice={"type": "tool", "name": "canonicalize_phrases"},
            messages=[{"role": "user", "content": f"Phrases:\n{numbered}"}],
        )
    except Exception as e:
        raise CanonicalizationError(f"Claude API call failed: {e}") from e

    result: list[Any] | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "canonicalize_phrases":
            result = block.input.get("canonical_forms")
            break

    if result is None:
        raise CanonicalizationError("Model response did not include canonical forms")
    if len(result) != len(phrases):
        raise CanonicalizationError(
            f"Expected {len(phrases)} canonical forms, got {len(result)} - discarding to avoid misalignment"
        )

    return [str(c) for c in result]


def canonicalize_or_fallback(phrases: list[str]) -> list[str]:
    """Same as canonicalize_phrases, but never raises - falls back to the
    original phrases (i.e. no cross-language benefit for this batch) on any
    failure. This is what callers should use in practice; the exception-raising
    version exists mainly for testing."""
    if not phrases:
        return []
    try:
        return canonicalize_phrases(phrases)
    except CanonicalizationError:
        return list(phrases)


CLASSIFY_CANONICALIZE_TOOL = {
    "name": "classify_and_canonicalize_phrases",
    "description": (
        "Record an English canonical form, a hard/soft skill classification, and a language code "
        "(if applicable) for each input phrase, in the same order."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical_form": {
                            "type": "string",
                            "description": (
                                "English canonical form of the phrase, e.g. 'vizuallaşdırma' -> "
                                "'data visualization'; 'MS Excel' -> 'Microsoft Excel'."
                            ),
                        },
                        "skill_type": {
                            "type": "string",
                            "enum": ["hard", "soft"],
                            "description": (
                                "'hard' for a technical/professional skill, tool, technology, "
                                "certification, or other measurable competency (e.g. Python, "
                                "Microsoft Excel, accounting, welding, English B2). 'soft' for an "
                                "interpersonal/behavioral trait not tied to a specific tool or "
                                "technical competency (e.g. communication, teamwork, leadership, "
                                "adaptability, time management, attention to detail, stress "
                                "tolerance). Judge by the phrase's actual meaning, not its surface "
                                "wording - e.g. 'communication skills' is soft, but 'communication "
                                "protocols' (a networking topic) is hard."
                            ),
                        },
                        "language_code": {
                            "type": ["string", "null"],
                            "description": (
                                "If this phrase is a spoken/written language requirement (e.g. "
                                "'İngilis dili biliyi', 'Russian language - fluent'), the ISO 639-1 "
                                "two-letter code for that language (e.g. 'en', 'az', 'ru', 'tr'), "
                                "regardless of spelling/script. Null if the phrase is not about a "
                                "spoken/written language at all (e.g. a technical skill, a soft "
                                "skill, education, experience)."
                            ),
                        },
                    },
                    "required": ["canonical_form", "skill_type", "language_code"],
                },
            }
        },
        "required": ["results"],
    },
}

CLASSIFY_SYSTEM_PROMPT = """You process short skill/requirement phrases (which may be in Azerbaijani, \
Russian, English, or a mix) taken from a job posting. For each phrase, in order, you do three things:

1. Produce a clean, standard English canonical form for text matching - same rules as plain \
translation/normalization: translate non-English phrases to English, normalize already-English \
phrases to their common standard form (e.g. "excel" -> "Microsoft Excel"), keep each form short (a \
few words, not a sentence).
2. Classify the phrase as a "hard" skill (a technical/professional skill, tool, technology, \
certification, or other measurable competency) or a "soft" skill (an interpersonal/behavioral trait, \
not tied to any specific tool or technical competency). Judge by what the phrase actually means in \
context, not just how it reads - a soft-skill-shaped phrase can be a technical term (e.g. \
"communication protocols" is a hard networking skill, not the soft skill "communication").
3. If the phrase is a spoken/written language requirement, set language_code to that language's ISO \
639-1 code (e.g. "İngilis dili" / "İnglis dili" / "English fluency" all -> "en") regardless of how \
the language's name is spelled or misspelled on the posting - downstream matching relies on this \
code, not the raw spelling. Set it to null for anything that isn't a language requirement.

Preserve the EXACT order and count of the input: output exactly one result per input phrase. Always \
respond by calling the classify_and_canonicalize_phrases tool."""


def classify_and_canonicalize_phrases(phrases: list[str]) -> tuple[list[str], list[str], list[str | None]]:
    """Like canonicalize_phrases, but also classifies each phrase as a "hard" or
    "soft" skill and, if it's a language requirement, resolves its language
    code - all in the same call, so a vacancy's requirements get everything in
    one API round-trip. Returns (canonical_forms, skill_types, language_codes),
    all in the same order/count as `phrases`.

    Used only for a vacancy's required_skills/preferred_skills - candidate
    skills don't need this classification, since it's the vacancy side that
    decides what gets excluded from scoring (see skill_matcher.py). Candidate
    resumes get their own language_code per language via ai_structurer.py
    instead, since that's a separate AI call with its own schema.
    """
    if not phrases:
        return [], [], []

    client = _get_client()
    numbered = "\n".join(f"{i + 1}. {phrase}" for i, phrase in enumerate(phrases))

    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=CLASSIFY_SYSTEM_PROMPT,
            tools=[CLASSIFY_CANONICALIZE_TOOL],
            tool_choice={"type": "tool", "name": "classify_and_canonicalize_phrases"},
            messages=[{"role": "user", "content": f"Phrases:\n{numbered}"}],
        )
    except Exception as e:
        raise CanonicalizationError(f"Claude API call failed: {e}") from e

    results: list[Any] | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_and_canonicalize_phrases":
            results = block.input.get("results")
            break

    if results is None:
        raise CanonicalizationError("Model response did not include results")
    if len(results) != len(phrases):
        raise CanonicalizationError(
            f"Expected {len(phrases)} results, got {len(results)} - discarding to avoid misalignment"
        )

    canonical_forms: list[str] = []
    skill_types: list[str] = []
    language_codes: list[str | None] = []
    for item in results:
        if not isinstance(item, dict) or "canonical_form" not in item or "skill_type" not in item:
            raise CanonicalizationError(f"Malformed result entry, discarding to avoid misalignment: {item!r}")
        canonical_forms.append(str(item["canonical_form"]))
        skill_type = item["skill_type"]
        skill_types.append(skill_type if skill_type in ("hard", "soft") else "hard")
        language_code = item.get("language_code")
        language_codes.append(str(language_code) if language_code else None)

    return canonical_forms, skill_types, language_codes


def classify_and_canonicalize_or_fallback(phrases: list[str]) -> tuple[list[str], list[str], list[str | None]]:
    """Same as classify_and_canonicalize_phrases, but never raises - falls back
    to the original phrases (no cross-language benefit for this batch), "hard"
    for every phrase, and None for every language_code on any failure.
    Defaulting to "hard" (not "soft") and None (not a guessed code) matters:
    an AI outage should never silently drop a real requirement from scoring or
    misroute it - a None language_code just means the requirement falls back
    to the alias-based language_matcher.detect_language_code() instead, the
    same principle used elsewhere for AI-dependent steps."""
    if not phrases:
        return [], [], []
    try:
        return classify_and_canonicalize_phrases(phrases)
    except CanonicalizationError:
        return list(phrases), ["hard"] * len(phrases), [None] * len(phrases)
