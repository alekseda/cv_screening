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
    """Finds the graduation year of a *specific* degree level from the raw
    education array.

    This exists because `highest_completed_degree_graduation_year` answers a
    different question: someone with a 2020 bachelor's and a 2023 master's
    reports 2023, which is useless for checking "did they finish their
    bachelor's in 2024/2025". So we look up the matching entry directly.
    """
    target = normalize(target_level)
    best_year: int | None = None

    for entry in structured.get("education") or []:
        if not isinstance(entry, dict):
            continue
        degree = normalize(entry.get("degree") or "")
        if not degree or target not in degree:
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

        # Path 2: already completed exactly that level, within the window.
        if candidate_level == requirement:
            grad_year = find_degree_graduation_year(structured, requirement)
            if grad_year is None:
                grad_year = candidate_fields.get("highest_completed_degree_graduation_year")
            result["candidate_degree_graduation_year"] = grad_year

            if _in_window(grad_year):
                result.update(
                    passed=True,
                    reason=f"Completed {requirement}" + (f" in {grad_year}" if grad_year else ""),
                )
            else:
                result.update(
                    passed=False,
                    reason=(
                        f"Completed {requirement} in {grad_year}, outside the "
                        f"{year_min}-{year_max} window this posting accepts"
                    ),
                )
            return result

        # Deliberately no "higher degree satisfies it" fallback here: this
        # posting wants a current student or fresh graduate at this level,
        # and someone with an unrelated higher degree from years ago isn't that.
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

    if candidate_rank < 0:
        # Extraction may leave education_level unset for a CV that lists an
        # in-progress-looking degree whose end year has actually arrived
        # (e.g. "2022-2026" read in 2026). Fall back to the raw education
        # entries rather than failing someone who has in fact graduated.
        for entry in structured.get("education") or []:
            if not isinstance(entry, dict) or not is_degree_completed(entry):
                continue
            level = degree_text_to_level(entry.get("degree") or "")
            if level and _rank(level) > candidate_rank:
                candidate_rank = _rank(level)
                candidate_level = level
        result["candidate_education_level"] = candidate_level

    if candidate_rank < 0:
        result.update(passed=False, reason="No completed education level could be determined from the resume")
        return result

    if candidate_rank >= required_rank:
        result.update(
            passed=True,
            reason=f"Holds {candidate_level}, which meets the {requirement} requirement",
        )
    else:
        result.update(
            passed=False,
            reason=f"Holds {candidate_level}, below the required {requirement}",
        )
    return result
