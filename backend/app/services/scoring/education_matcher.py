"""
Education requirement checking.

Two distinct modes, because "must hold a bachelor's degree" and "must be a
final-year student or a 2024/2025 graduate" are genuinely different questions:

1. Standard mode (accepts_current_students = False): the usual seniority
   ladder - a higher completed degree satisfies a lower requirement.

2. Enrollment-window mode (accepts_current_students = True): typical of
   internship/graduate-programme postings. Here an unrelated higher degree
   earned years ago does NOT auto-qualify - the posting wants someone who is
   currently at, or has just finished, that specific level. Only two things
   pass: currently enrolled at the required level, or having completed exactly
   that level within the stated graduation-year window.
"""
from datetime import date

from app.services.scoring.text_normalization import normalize

# Ordered low to high. "none" means the vacancy has no education requirement.
EDUCATION_HIERARCHY = ["none", "high_school", "bachelor", "master", "phd"]

# Degree names as they actually appear on AZ/RU/EN CVs, mapped to their tier.
# Without this, an Azerbaijani "Bakalavr" never resolved to "bachelor" and a
# genuinely graduated candidate read as having no determinable education level.
DEGREE_ALIASES: dict[str, str] = {
    "bachelor": "bachelor", "bachelors": "bachelor", "bakalavr": "bachelor",
    "baklavr": "bachelor", "bakalavriat": "bachelor", "бакалавр": "bachelor",
    "undergraduate": "bachelor", "lisans": "bachelor",
    "master": "master", "masters": "master", "magistr": "master",
    "magistratura": "master", "магистр": "master", "mba": "master",
    "phd": "phd", "doctorate": "phd", "doktorantura": "phd", "doktorant": "phd",
    "doktora": "phd", "аспирантура": "phd", "докторантура": "phd",
    "high": "high_school", "school": "high_school", "orta": "high_school",
    "mekteb": "high_school", "kollec": "high_school", "college": "high_school",
    "среднее": "high_school", "школа": "high_school",
}


def degree_text_to_level(text: str) -> str | None:
    """Resolves a free-text degree name in any of the three languages to a
    hierarchy tier, or None if it isn't recognisable."""
    if not text:
        return None
    tokens = normalize(text).split()
    best: str | None = None
    for token in tokens:
        level = DEGREE_ALIASES.get(token)
        if level and (best is None or EDUCATION_HIERARCHY.index(level) > EDUCATION_HIERARCHY.index(best)):
            best = level
    return best


def _rank(level: str | None) -> int:
    """Position in the hierarchy; -1 for unknown/unstated levels."""
    if not level:
        return -1
    try:
        return EDUCATION_HIERARCHY.index(level)
    except ValueError:
        return -1


# Institutions likely to be tertiary (university-level), used only as a
# fallback signal (see _infer_untitled_degree_level) when a CV lists an
# institution + dates but never states a degree name anywhere - extremely
# common on AZ/RU/EN CVs, which often just write "University Name, 2017-2021"
# and treat the degree as implied. Matched as a substring of the normalized
# institution name.
_UNIVERSITY_MARKERS = {
    "university", "universitet", "institute", "institut", "academy", "akademiya",
    "университет", "институт", "академия",
}

# Rules an institution OUT as vocational/secondary even if it also contains a
# word from _UNIVERSITY_MARKERS (e.g. a vocational college named "... Institute
# of ...").
_NON_TERTIARY_MARKERS = {
    "school", "college", "kollec", "texnikum", "mekteb", "училище", "школа", "колледж",
}

# A bachelor's programme in Azerbaijan (bakalavriat) is normally 4 years;
# allow slack either side for part-time/extended/slightly-short records
# rather than requiring an exact match.
_PLAUSIBLE_BACHELOR_YEARS = range(3, 7)  # 3..6 inclusive


def _infer_untitled_degree_level(entry: dict) -> str | None:
    """Best-effort fallback for an education entry that names an institution
    and dates but never states a degree at all (no "Bachelor"/"Bakalavr"/etc
    anywhere in the resume) - very common on AZ/RU/EN CVs, which often just
    write "University Name, 2017-2021" and consider the degree implied.

    Only fires for a university/institute/academy (not a school or college)
    with a plausible bachelor's-length span - deliberately narrow so it can't
    accidentally credit a short course or certificate program with a degree.
    Caller is responsible for checking the entry has actually finished
    (is_degree_completed) before relying on this.
    """
    institution = normalize(entry.get("institution") or "")
    if not institution:
        return None
    if not any(marker in institution for marker in _UNIVERSITY_MARKERS):
        return None
    if any(marker in institution for marker in _NON_TERTIARY_MARKERS):
        return None

    start, end = entry.get("start_date"), entry.get("end_date")
    if not (isinstance(start, str) and isinstance(end, str)):
        return None
    start_year, end_year = start.strip()[:4], end.strip()[:4]
    if not (start_year.isdigit() and end_year.isdigit()):
        return None

    if (int(end_year) - int(start_year)) not in _PLAUSIBLE_BACHELOR_YEARS:
        return None

    return "bachelor"


def is_degree_completed(entry: dict, today_year: int | None = None) -> bool:
    """True if this education entry's programme has finished.

    A stated end year that has already arrived counts as completed, even if
    the CV never says "graduated" - a candidate listing "2022-2026" in 2026 has
    finished, and treating them as still-enrolled (as an earlier version did)
    wrongly failed them against a completed-degree requirement.
    """
    today_year = today_year or date.today().year
    end_date = entry.get("end_date")
    if not end_date or not isinstance(end_date, str):
        return False
    year_part = end_date.strip()[:4]
    if not year_part.isdigit():
        return False
    return int(year_part) <= today_year


def find_degree_graduation_year(structured: dict, target_level: str) -> int | None:
    """Finds the graduation year of a *specific*, COMPLETED degree level from
    the raw education array.

    This exists because `highest_completed_degree_graduation_year` answers a
    different question: someone with a 2020 bachelor's and a 2023 master's
    reports 2023, which is useless for checking "did they finish their
    bachelor's in 2024/2025". So we look up the matching entry directly.

    Uses degree_text_to_level (the same AZ/RU/EN alias table as everywhere
    else in this module) rather than a raw substring match against the
    entry's own degree text - a bachelor's entry literally reading "Bakalavr"
    or "бакалавр" never contains the substring "bachelor", so the naive
    version silently failed to find a degree that plainly is one.
    """
    best_year: int | None = None

    for entry in structured.get("education") or []:
        if not isinstance(entry, dict):
            continue
        if degree_text_to_level(entry.get("degree") or "") != target_level:
            continue
        if not is_degree_completed(entry):
            continue

        end_date = entry.get("end_date")
        if not end_date or not isinstance(end_date, str):
            continue
        # end_date may be YYYY, YYYY-MM or YYYY-MM-DD - the year is the leading 4 digits
        year_part = end_date.strip()[:4]
        if not year_part.isdigit():
            continue
        year = int(year_part)
        # If a candidate somehow lists the same level twice, take the most recent.
        if best_year is None or year > best_year:
            best_year = year

    return best_year


def check_education(structured: dict, candidate_fields: dict, vacancy) -> dict:
    """Returns an audit record: passed/failed, plus why.

    `candidate_fields` carries the promoted column values (education_level,
    currently_enrolled, current_degree_level, expected_graduation_year) so this
    works both from a live Candidate row and from structured data alone.
    """
    requirement = vacancy.education_requirement or "none"
    result: dict = {
        "requirement": requirement,
        "accepts_current_students": bool(vacancy.accepts_current_students),
        "graduation_year_window": [vacancy.graduation_year_min, vacancy.graduation_year_max],
        "candidate_education_level": candidate_fields.get("education_level"),
        "candidate_currently_enrolled": candidate_fields.get("currently_enrolled"),
        "candidate_current_degree_level": candidate_fields.get("current_degree_level"),
        "candidate_expected_graduation_year": candidate_fields.get("expected_graduation_year"),
        "passed": False,
        "reason": "",
    }

    if requirement == "none":
        result.update(passed=True, reason="Vacancy specifies no education requirement")
        return result

    candidate_level = candidate_fields.get("education_level")

    # --- Enrollment-window mode ---------------------------------------------
    if vacancy.accepts_current_students:
        year_min = vacancy.graduation_year_min
        year_max = vacancy.graduation_year_max

        def _in_window(year: int | None) -> bool:
            """No window configured means any year is acceptable."""
            if year is None:
                return year_min is None and year_max is None
            if year_min is not None and year < year_min:
                return False
            if year_max is not None and year > year_max:
                return False
            return True

        # Path 1: currently enrolled at exactly the required level.
        if candidate_fields.get("currently_enrolled") and candidate_fields.get("current_degree_level") == requirement:
            expected = candidate_fields.get("expected_graduation_year")
            if _in_window(expected):
                result.update(
                    passed=True,
                    reason=(
                        f"Currently enrolled in a {requirement} programme"
                        + (f", expected graduation {expected}" if expected else "")
                    ),
                )
                return result
            result.update(
                passed=False,
                reason=(
                    f"Currently enrolled in a {requirement} programme but expected graduation "
                    f"{expected} falls outside the {year_min}-{year_max} window"
                ),
            )
            return result

        # Path 2: holds a COMPLETED degree at exactly the required level,
        # within the window - checked against the specific education entries,
        # not just the candidate's overall/current highest level. This matters
        # because someone who finished their bachelor's in-window and has
        # SINCE started a master's still education_level="master" overall -
        # they're a genuine 2024/2025 bachelor's graduate regardless of what
        # they've moved on to, and shouldn't be read as merely "holds a
        # higher degree" (that rejection, below, is for someone with NO
        # qualifying-level degree at all - an unrelated advanced degree from
        # years ago, nothing at the target level).
        grad_year = find_degree_graduation_year(structured, requirement)
        if grad_year is None and candidate_level == requirement:
            # No matching entry found in the raw education array (or its
            # dates weren't parseable), but the AI's own overall-level summary
            # agrees - fall back to that rather than failing someone whose
            # education section just didn't structure cleanly.
            grad_year = candidate_fields.get("highest_completed_degree_graduation_year")

        if grad_year is not None:
            result["candidate_degree_graduation_year"] = grad_year
            if _in_window(grad_year):
                result.update(passed=True, reason=f"Completed {requirement} in {grad_year}")
            else:
                result.update(
                    passed=False,
                    reason=(
                        f"Completed {requirement} in {grad_year}, outside the "
                        f"{year_min}-{year_max} window this posting accepts"
                    ),
                )
            return result

        # Deliberately no "higher degree satisfies it" fallback beyond the
        # checks above: this posting wants a current student or recent
        # graduate AT the required level (whether or not that's still their
        # current focus), and someone with no qualifying-level degree at
        # all - just an unrelated higher one - isn't that.
        if _rank(candidate_level) > _rank(requirement):
            result.update(
                passed=False,
                reason=(
                    f"Holds a higher degree ({candidate_level}) than the {requirement} level this "
                    "posting targets, and this posting is aimed at current students / recent graduates "
                    "at that specific level"
                ),
            )
            return result

        result.update(
            passed=False,
            reason=f"Not currently enrolled in, nor a recent graduate of, a {requirement} programme",
        )
        return result

    # --- Standard mode -------------------------------------------------------
    candidate_rank = _rank(candidate_level)
    required_rank = _rank(requirement)

    inferred_note: str | None = None
    if candidate_rank < 0:
        # Extraction may leave education_level unset for a CV that lists an
        # in-progress-looking degree whose end year has actually arrived
        # (e.g. "2022-2026" read in 2026), or that never states a degree name
        # at all (just "University Name, 2017-2021" - very common on AZ/RU/EN
        # CVs). Fall back to the raw education entries rather than failing
        # someone who has in fact graduated.
        for entry in structured.get("education") or []:
            if not isinstance(entry, dict) or not is_degree_completed(entry):
                continue
            level = degree_text_to_level(entry.get("degree") or "")
            note = None
            if not level:
                level = _infer_untitled_degree_level(entry)
                if level:
                    note = (
                        f"inferred from {entry.get('institution') or 'an unnamed institution'} "
                        f"({entry.get('start_date')}–{entry.get('end_date')}) - no degree stated explicitly"
                    )
            if level and _rank(level) > candidate_rank:
                candidate_rank = _rank(level)
                candidate_level = level
                inferred_note = note
        result["candidate_education_level"] = candidate_level

    if candidate_rank < 0:
        result.update(passed=False, reason="No completed education level could be determined from the resume")
        return result

    suffix = f" ({inferred_note})" if inferred_note else ""
    if candidate_rank >= required_rank:
        result.update(
            passed=True,
            reason=f"Holds {candidate_level}{suffix}, which meets the {requirement} requirement",
        )
    else:
        result.update(
            passed=False,
            reason=f"Holds {candidate_level}{suffix}, below the required {requirement}",
        )
    return result
