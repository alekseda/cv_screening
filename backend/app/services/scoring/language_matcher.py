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
_register("en", "english", "ingilis", "ingilis dili", "ingiliscə", "английский", "английский язык")
_register("tr", "turkish", "turk", "türk", "türkcə", "turkce", "турецкий")
_register("de", "german", "alman", "немецкий")
_register("fr", "french", "fransiz", "fransız", "французский")
_register("ar", "arabic", "ereb", "ərəb", "арабский")
_register("fa", "persian", "farsi", "fars", "персидский")


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


def is_language_requirement(requirement: str) -> bool:
    """True if this requirement is about speaking a language, so the caller can
    route it to this matcher instead of generic skill matching."""
    return detect_language_code(requirement) is not None


def candidate_language_codes(structured: dict) -> dict[str, str]:
    """{language_code: proficiency_as_written} for everything the CV lists."""
    found: dict[str, str] = {}
    for entry in structured.get("languages_spoken") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("language")
        if not name:
            continue
        code = detect_language_code(str(name))
        if code:
            found[code] = str(entry.get("proficiency") or "").strip()
    return found


def match_language_requirement(requirement: str, structured: dict) -> dict:
    """Checks one language requirement against the CV's language list.

    Deliberately checks only WHETHER the language is listed, not the
    proficiency level: proficiency is written in wildly inconsistent ways
    across three languages ("Ana dili", "First Language", "B2", "sərbəst",
    "Yaxşı / Upper-Intermediate"), and silently failing a candidate on a
    level we parsed loosely would be worse than not checking it. The stated
    level is surfaced in the breakdown for the recruiter to judge.
    """
    code = detect_language_code(requirement)
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
    else:
        result["missing_tokens"] = [code]

    return result
