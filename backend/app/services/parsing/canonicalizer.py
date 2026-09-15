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
