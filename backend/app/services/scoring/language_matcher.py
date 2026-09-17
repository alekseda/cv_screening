"""
Language requirements get their own matcher rather than going through generic
skill matching, for two reasons found in real testing:

1. Language NAMES don't token-match across languages. A requirement written as
   "Azərbaycan dilində səlis danışıq" never matched a CV listing "Azerbaijan /
   First Language" - "azerbaycan" and "azerbaijan" are different strings, so
   the candidate's own native language scored as missing.
2. Generic matching latched onto the wrong word. "Rus dili biliyi" against a CV
   listing "Russian (B1)" matched the token "russian" but counted "language" as
   missing, dropping the ratio to 0.5 and failing a requirement the candidate
   clearly met.

Both are fixed by resolving language names to ISO-style codes on each side and
comparing those, ignoring the filler words entirely.

Two ways a language gets resolved to a code, tried in this order:
1. AI-resolved: ai_structurer.py (candidate side) and canonicalizer.py's
   classify_and_canonicalize (vacancy side) already ask the model to resolve
   each language to its ISO 639-1 code as part of a call that's happening
   anyway, so a misspelling ("İnglis dili" instead of "İngilis dili") or an
   unlisted alias never causes a false "missing" - the model generalizes to
   spellings this file was never updated for.
2. LANGUAGE_ALIASES fallback: used when the AI-resolved code is missing (older
   data parsed before this existed, or the AI call failed and fell back). A
   fixed alias list can't generalize to new spellings, which is exactly the
   gap AI resolution above is meant to close - this is the safety net, not
   the primary path.
"""
from app.services.scoring.text_normalization import normalize

# Every spelling we've seen for each language, across AZ/RU/EN CVs and postings.
# Keys are normalized (see normalize()) before lookup, so accents/scripts don't matter.
LANGUAGE_ALIASES: dict[str, str] = {}


def _register(code: str, *names: str) -> None:
    for name in names:
        LANGUAGE_ALIASES[normalize(name)] = code


_register("az", "azerbaijani", "azerbaycan", "azərbaycan", "azerbaycanca", "azərbaycanca",
          "azeri", "azerbaijan", "азербайджанский", "азербайджанский язык")
_register("ru", "russian", "rus", "русский", "русский язык", "rusca", "rusça")
_register("en", "english", "ingilis", "ingilis dili", "ingiliscə", "inglis", "inglis dili", "inglisce",
          "английский", "английский язык")
_register("tr", "turkish", "turk", "türk", "türkcə", "turkce", "турецкий")
_register("de", "german", "alman", "немецкий")
_register("fr", "french", "fransiz", "fransız", "французский")
_register("ar", "arabic", "ereb", "ərəb", "арабский")
_register("fa", "persian", "farsi", "fars", "персидский")

# Azerbaijani is the market's default working language, so candidates
# routinely leave it off their CV's language list - they list what they
# consider notable (English, Russian, etc.) and treat Azerbaijani as a
# given. Counting an unlisted Azerbaijani requirement as "missing" would
# penalize normal CV-writing habits rather than actual language ability, so
# it's assumed satisfied by default when the CV doesn't mention it either
# way. Listed languages that AREN'T in this set still require an explicit
# mention, same as before.
AUTO_ASSUMED_LANGUAGES = {"az"}


def detect_language_code(text: str) -> str | None:
    """Finds a known language name anywhere in a phrase and returns its code.

    Works on phrases, not just bare names, so "Azərbaycan dilində səlis danışıq
    və yazı bacarığı" resolves to "az" - the surrounding words about fluency and
    writing don't interfere.
    """
    if not text:
        return None
    normalized = normalize(text)
    if normalized in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[normalized]

    # Longest alias first, so "azerbaycan" wins over a shorter substring match.
    for alias in sorted(LANGUAGE_ALIASES, key=len, reverse=True):
        if alias and alias in normalized:
            return LANGUAGE_ALIASES[alias]
    return None


def is_language_requirement(requirement: str, language_code: str | None = None) -> bool:
    """True if this requirement is about speaking a language, so the caller can
    route it to this matcher instead of generic skill matching.

    `language_code` is the AI-resolved code for this requirement, if the
    caller has one (see Vacancy.required_skills_language_code) - passing it
    skips the alias-list guesswork entirely. Falls back to detect_language_code
    when it's None (not classified, or classification failed)."""
    if language_code:
        return True
    return detect_language_code(requirement) is not None


def candidate_language_codes(structured: dict) -> dict[str, str]:
    """{language_code: proficiency_as_written} for everything the CV lists.

    Prefers each entry's AI-resolved language_code (see ai_structurer.py) over
    guessing from the language name - only falls back to the alias list
    (detect_language_code) when language_code is missing, e.g. structured_data
    parsed before that field existed, or the field came back null/unrecognized."""
    found: dict[str, str] = {}
    for entry in structured.get("languages_spoken") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("language")
        if not name:
            continue
        code = entry.get("language_code") or detect_language_code(str(name))
        if code:
            found[code] = str(entry.get("proficiency") or "").strip()
    return found


def match_language_requirement(requirement: str, structured: dict, requirement_language_code: str | None = None) -> dict:
    """Checks one language requirement against the CV's language list.

    Deliberately checks only WHETHER the language is listed, not the
    proficiency level: proficiency is written in wildly inconsistent ways
    across three languages ("Ana dili", "First Language", "B2", "sərbəst",
    "Yaxşı / Upper-Intermediate"), and silently failing a candidate on a
    level we parsed loosely would be worse than not checking it. The stated
    level is surfaced in the breakdown for the recruiter to judge.

    `requirement_language_code` is this requirement's AI-resolved code (see
    Vacancy.required_skills_language_code/preferred_skills_language_code) -
    used when present, falling back to detect_language_code(requirement)
    (the alias list) when it's None.

    Exception: a language in AUTO_ASSUMED_LANGUAGES (currently just
    Azerbaijani) that the CV doesn't list at all is treated as satisfied
    rather than missing - see the comment on that set.
    """
    code = requirement_language_code or detect_language_code(requirement)
    result = {
        "requirement": requirement,
        "matched": False,
        "match_ratio": 0.0,
        "matched_tokens": [],
        "missing_tokens": [],
        "matched_sources": [],
        "matched_via": [],
        "check": "language",
        "language_code": code,
    }

    if code is None:
        return result

    candidate_languages = candidate_language_codes(structured)
    if code in candidate_languages:
        proficiency = candidate_languages[code] or "level not stated"
        result.update(
            matched=True,
            match_ratio=1.0,
            matched_tokens=[code],
            matched_sources=["language"],
            matched_via=[f"{code.upper()}: {proficiency}"],
        )
    elif code in AUTO_ASSUMED_LANGUAGES:
        result.update(
            matched=True,
            match_ratio=1.0,
            matched_tokens=[code],
            matched_sources=["assumed"],
            matched_via=[
                f"{code.upper()}: not listed on the CV - assumed, since candidates in this "
                "market don't reliably list Azerbaijani explicitly"
            ],
            assumed=True,
        )
    else:
        result["missing_tokens"] = [code]

    return result
