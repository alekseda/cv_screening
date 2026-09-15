"""
Shared text normalization for all rule-based matching.

Lives in its own module because both skill_matcher and language_matcher need
it, and skill_matcher routes language requirements to language_matcher - having
normalize() in either one would create a circular import.
"""
import re
import unicodedata

# Azerbaijani letters that Unicode NFKD does NOT decompose (they're distinct
# letters, not accented Latin ones) and so need explicit transliteration.
# Without this, "üzrə" normalises to "uzrə" and never matches a stopword entry.
AZ_TRANSLITERATION = str.maketrans({
    "ə": "e", "ı": "i", "ğ": "g", "ş": "s", "ç": "c", "ö": "o", "ü": "u",
    "Ə": "e", "I": "i", "Ğ": "g", "Ş": "s", "Ç": "c", "Ö": "o", "Ü": "u",
})


def normalize(text: str) -> str:
    """Lowercase, transliterate Azerbaijani letters, strip remaining diacritics,
    and collapse punctuation to spaces.

    Normalising away diacritics matters here: Azerbaijani text is inconsistently
    written with and without them (e.g. "təcrübə" vs "tecrube"), and the two
    forms should match each other."""
    if not text:
        return ""
    text = text.lower().translate(AZ_TRANSLITERATION)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
