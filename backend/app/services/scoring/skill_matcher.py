"""
Rule-based matching of a vacancy's requirement phrases against a candidate's
skills. Deliberately NOT semantic/AI-driven: this is the auditable half of the
score, so every match must be explainable as "these words matched that text".
The semantic half of the score is handled separately by embeddings.

The tricky part is that JD-extracted requirements are phrases, not tidy tags -
e.g. "MS Excel - Pivot tables and core formulas" needs to match a candidate
skill listed simply as "Excel". Exact string equality would fail almost
everything, so matching is token-overlap based, with each match reporting
which candidate text satisfied it.
"""
from app.services.scoring.language_matcher import is_language_requirement, match_language_requirement
from app.services.scoring.text_normalization import normalize

# Words that carry no matching signal - dropping them stops "knowledge of SQL"
# from matching "knowledge of Python" on the shared filler words. Includes
# Azerbaijani and Russian equivalents since requirements arrive in all three
# languages. These are normalized at import time (see _NORMALIZED_STOPWORDS)
# so they're compared on exactly the same footing as incoming text.
_RAW_STOPWORDS = {
    # English
    "and", "or", "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by",
    "is", "are", "be", "as", "from", "good", "strong", "basic", "level", "knowledge",
    "skills", "skill", "experience", "ability", "abilities", "proficiency", "proficient",
    "understanding", "working", "excellent", "solid", "familiar", "familiarity",
    "using", "use", "must", "have", "including", "etc", "language", "languages",
    "written", "spoken", "fluent", "fluency", "native",
    # Azerbaijani
    "və", "üzrə", "ilə", "bilik", "bilikləri", "biliklər", "biliyi", "səviyyədə",
    "yaxşı", "əsas", "bacarığı", "bacarıq", "bacarıqları", "təcrübə", "təcrübəsi", "olmaq",
    "üzərində", "sahəsində", "haqqında", "üstünlükdür", "arzuolunandır",
    "dil", "dili", "dilində", "danışıq", "yazı", "səlis",
    # Russian
    "и", "или", "в", "на", "с", "по", "для", "знание", "знания", "уровень", "опыт",
    "навыки", "навык", "хорошее", "хороший", "базовые", "основы", "владение", "умение",
    "язык", "языка", "языком",
}


# Normalized once at import so stopwords and incoming text can never drift apart.
_NORMALIZED_STOPWORDS = {normalize(w) for w in _RAW_STOPWORDS}

# A requirement matches if this fraction of its meaningful tokens are present
# in the candidate's text. 0.6 tolerates a requirement phrase carrying extra
# qualifying words the candidate's own listing doesn't repeat verbatim.
TOKEN_OVERLAP_THRESHOLD = 0.6


def tokenize(text: str) -> set[str]:
    """Meaningful tokens only - stopwords and 1-character fragments dropped."""
    return {t for t in normalize(text).split() if t not in _NORMALIZED_STOPWORDS and len(t) > 1}


def _candidate_corpus(structured: dict) -> list[tuple[str, str]]:
    """Everything on the resume a requirement could legitimately be satisfied by,
    as (source_label, text) pairs so a match can report where it came from.

    Includes job titles/descriptions and certificates, not just the skills list:
    a candidate who describes building dashboards in a job description has that
    skill whether or not they also listed it under "Skills". Also includes
    skills_canonical (English canonical forms, same order as skills - see
    canonicalizer.py) so a requirement in a different language can still match
    a skill written in, say, Azerbaijani, and vice versa."""
    corpus: list[tuple[str, str]] = []

    for skill in structured.get("skills") or []:
        if skill:
            corpus.append(("skills", str(skill)))

    for skill in structured.get("skills_canonical") or []:
        if skill:
            corpus.append(("skills_canonical", str(skill)))

    for cert in structured.get("certificates") or []:
        name = cert.get("name") if isinstance(cert, dict) else None
        if name:
            corpus.append(("certificate", str(name)))

    for lang in structured.get("languages_spoken") or []:
        if not isinstance(lang, dict) or not lang.get("language"):
            continue
        proficiency = lang.get("proficiency") or ""
        corpus.append(("language", f"{lang['language']} {proficiency}".strip()))

    for job in structured.get("work_history") or []:
        if not isinstance(job, dict):
            continue
        text = " ".join(filter(None, [job.get("title"), job.get("description")]))
        if text.strip():
            corpus.append(("work_history", text))

    for vol in structured.get("volunteer_experience") or []:
        if not isinstance(vol, dict):
            continue
        text = " ".join(filter(None, [vol.get("role"), vol.get("description")]))
        if text.strip():
            corpus.append(("volunteer_experience", text))

    for edu in structured.get("education") or []:
        if not isinstance(edu, dict):
            continue
        text = " ".join(filter(None, [edu.get("degree"), edu.get("field_of_study"), edu.get("institution")]))
        if text.strip():
            corpus.append(("education", text))

    return corpus


def corpus_text(structured: dict) -> str:
    """Joins the same resume text the literal token matcher draws from into
    one block, for handing to an LLM as context (see skill_relevance_judge.py).
    Kept on top of _candidate_corpus so the semantic fallback sees exactly
    what the literal matcher saw - nothing more, nothing less."""
    return "\n".join(f"[{label}] {text}" for label, text in _candidate_corpus(structured))


def _match_ratio(tokens: set[str], corpus: list[tuple[str, str]]) -> tuple[float, dict[str, list[tuple[str, str]]]]:
    """Fraction of `tokens` found somewhere in the corpus, plus which corpus
    entries contributed each token (for the audit trail)."""
    if not tokens:
        return 0.0, {}
    token_sources: dict[str, list[tuple[str, str]]] = {}
    for source_label, text in corpus:
        for token in tokenize(text) & tokens:
            token_sources.setdefault(token, []).append((source_label, text))
    return len(token_sources) / len(tokens), token_sources


def match_requirement(requirement: str, requirement_canonical: str, structured: dict) -> dict:
    """Checks one requirement phrase against the whole resume.

    Matching is done against the resume's combined vocabulary rather than
    each entry in isolation, because a single requirement legitimately spans
    several listed items - "MS Excel (Pivot, formulas)" is satisfied by a
    candidate who lists "MS Excel" and "Pivot Tables" as two separate skills.

    `requirement_canonical` is the requirement's own English canonical form
    (same concept as skills_canonical on the candidate side - see
    canonicalizer.py). The original phrase and its canonical form are scored
    SEPARATELY, and the better of the two is used - NOT their token union.
    Unioning would inflate the token count whenever the two phrasings share no
    literal words (the normal case across languages), making the match
    threshold harder to clear rather than easier, which would defeat the
    point. Scoring separately means a requirement matches if EITHER its
    original wording or its canonical translation lines up with the resume.

    Returns an audit record: whether it matched, which tokens overlapped, and
    which parts of the resume contributed them - so a recruiter can see
    exactly why the system called this a match.
    """
    result: dict = {
        "requirement": requirement,
        "matched": False,
        "match_ratio": 0.0,
        "matched_tokens": [],
        "missing_tokens": [],
        "matched_sources": [],
        "matched_via": [],
    }

    original_tokens = tokenize(requirement)
    canonical_tokens = tokenize(requirement_canonical) if requirement_canonical else set()
    if not original_tokens and not canonical_tokens:
        return result

    corpus = _candidate_corpus(structured)
    original_ratio, original_sources = _match_ratio(original_tokens, corpus)
    canonical_ratio, canonical_sources = _match_ratio(canonical_tokens, corpus)

    if canonical_ratio >= original_ratio:
        best_tokens, best_ratio, best_sources = canonical_tokens, canonical_ratio, canonical_sources
    else:
        best_tokens, best_ratio, best_sources = original_tokens, original_ratio, original_sources

    overlap = set(best_sources.keys())
    contributing = {label for entries in best_sources.values() for label, _ in entries}
    evidence: list[str] = []
    for entries in best_sources.values():
        for _, text in entries:
            snippet = text if len(text) <= 200 else text[:200] + "..."
            if snippet not in evidence:
                evidence.append(snippet)

    result.update(
        matched=best_ratio >= TOKEN_OVERLAP_THRESHOLD,
        match_ratio=round(best_ratio, 2),
        matched_tokens=sorted(overlap),
        missing_tokens=sorted(best_tokens - overlap),
        matched_sources=sorted(contributing),
        matched_via=evidence[:5],  # cap so a breakdown stays readable
    )
    return result


# Requirements that are really about education level or years of experience are
# NOT skills - they're already checked by their own dedicated components
# (education_requirement / min_experience_years on the vacancy). Matching them
# as skills too would both double-count them and produce nonsense results, since
# a phrase like "Ali təhsil" ("higher education") has nothing to match against
# in a candidate's skills list. Detected here and excluded from skill scoring.
_EDUCATION_MARKERS = {
    "ali", "tehsil", "education", "degree", "bachelor", "bakalavr", "master", "magistr",
    "diplom", "diploma", "universitet", "university", "phd", "doktorantura", "образование",
    "высшее", "бакалавр", "магистр",
}
_EXPERIENCE_MARKERS = {
    "il", "illik", "years", "year", "experience", "tecrube", "tecrubesi", "стаж", "лет", "года",
}


def is_non_skill_requirement(requirement: str) -> str | None:
    """Returns "education" / "experience" if this requirement belongs to a
    dedicated scoring component rather than skill matching, else None."""
    tokens = set(normalize(requirement).split())
    if tokens & _EDUCATION_MARKERS:
        return "education"
    # Experience requirements need a number to count ("minimum 2 il iş təcrübəsi"),
    # so a bare mention of "experience with X" is still treated as a skill.
    if tokens & _EXPERIENCE_MARKERS and any(t.isdigit() for t in tokens):
        return "experience"
    return None


def match_skill_list(
    requirements: list[str],
    requirement_canonicals: list[str] | None,
    structured: dict,
    requirement_types: list[str] | None = None,
    requirement_language_codes: list[str | None] | None = None,
) -> tuple[float, list[dict]]:
    """Runs every requirement against the resume.

    Requirements are routed by type rather than all going through token
    matching, because real postings mix genuine skills with things that have
    their own dedicated checks:
    - Language requirements go to language_matcher (name aliases across
      scripts, no proficiency-string guessing).
    - Education / years-of-experience requirements are EXCLUDED from skill
      scoring entirely - they're scored by their own components, and matching
      them here as well would double-count them.
    - Soft skills (communication, teamwork, leadership, etc. - classified by
      canonicalizer.classify_and_canonicalize_phrases) are also EXCLUDED: the
      whole point is that only hard/technical skills count toward
      required_skills_weight/preferred_skills_weight.
    - Everything else gets normal token matching.

    `requirement_canonicals` should be the same length/order as `requirements`
    (see Vacancy.required_skills_canonical/preferred_skills_canonical). If it's
    missing or mismatched in length, each requirement falls back to using
    itself as its own canonical form.

    `requirement_types` should likewise line up with `requirements` (see
    Vacancy.required_skills_type/preferred_skills_type), with each entry
    "hard" or "soft". If it's missing or mismatched in length (e.g. a vacancy
    saved before this classification existed), every requirement defaults to
    "hard" - i.e. still scored, since an unclassified requirement being
    dropped from scoring would be a worse failure mode than it being scored.

    `requirement_language_codes` should likewise line up with `requirements`
    (see Vacancy.required_skills_language_code/preferred_skills_language_code),
    each entry an ISO 639-1 code or None. Used to detect and match language
    requirements without depending on language_matcher's alias-spelling list -
    see the module docstring there. Missing/mismatched-length/None entries
    fall back to that alias list per-requirement, same as before this existed.

    Returns (fraction_matched, per_requirement_details). If every requirement
    was excluded (or the list was empty), scores 1.0 - a criterion that isn't
    being checked here shouldn't drag anyone's score down.
    """
    if not requirements:
        return 1.0, []

    if not requirement_canonicals or len(requirement_canonicals) != len(requirements):
        requirement_canonicals = list(requirements)

    if not requirement_types or len(requirement_types) != len(requirements):
        requirement_types = ["hard"] * len(requirements)

    if not requirement_language_codes or len(requirement_language_codes) != len(requirements):
        requirement_language_codes = [None] * len(requirements)

    details: list[dict] = []
    scored_count = 0
    matched_count = 0

    for req, canonical, skill_type, language_code in zip(
        requirements, requirement_canonicals, requirement_types, requirement_language_codes
    ):
        excluded_as = is_non_skill_requirement(req)
        if excluded_as:
            details.append({
                "requirement": req,
                "matched": None,
                "check": "excluded",
                "excluded_reason": (
                    f"Scored by the {excluded_as} component instead - not treated as a skill"
                ),
            })
            continue

        if skill_type == "soft":
            details.append({
                "requirement": req,
                "matched": None,
                "check": "excluded",
                "excluded_reason": "Soft skill - excluded from scoring",
            })
            continue

        if is_language_requirement(req, language_code):
            detail = match_language_requirement(req, structured, language_code)
        else:
            detail = match_requirement(req, canonical, structured)

        details.append(detail)
        scored_count += 1
        if detail["matched"]:
            matched_count += 1

    if scored_count == 0:
        return 1.0, details

    return matched_count / scored_count, details


def apply_semantic_fallback(
    score: float, details: list[dict], skill_relevances: dict[str, dict] | None
) -> tuple[float, list[dict]]:
    """Upgrades requirements the literal token matcher missed to "matched" if
    the (cached) LLM semantic judgement says the resume's substance actually
    demonstrates them - see skill_relevance_judge.py for why this exists and
    isn't an embedding comparison.

    `skill_relevances` is {requirement_text: {"matched": bool, "reason": str}},
    populated only for requirements that failed the literal pass (routes/
    matching.py's _skill_relevances_for). A requirement with no entry here -
    because it already matched literally, or because the judgement call
    failed - is left exactly as match_skill_list scored it.

    Mutates `details` in place (adding matched_via_semantic to any upgraded
    entry) and returns the recomputed (score, details) pair, mirroring
    match_skill_list's own return shape so callers can drop this in right
    after it.
    """
    if not skill_relevances:
        return score, details

    scored_count = 0
    matched_count = 0
    for detail in details:
        if detail.get("matched") is None:  # excluded (education/experience/soft skill)
            continue
        scored_count += 1
        if detail["matched"]:
            matched_count += 1
            continue

        judgement = skill_relevances.get(detail["requirement"])
        if judgement and judgement.get("matched"):
            detail["matched"] = True
            detail["matched_via_semantic"] = (
                judgement.get("reason") or "Matched by semantic judgement, not literal wording"
            )
            matched_count += 1

    if scored_count == 0:
        return score, details
    return matched_count / scored_count, details
