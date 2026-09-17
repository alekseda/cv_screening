"""
The scoring engine: one 0-100 score per candidate/vacancy pair, as a flat
weighted sum of six components.

The guiding constraint is auditability. Every number comes with a breakdown
explaining how it was reached - which requirements matched and via what
resume text, which are missing, how much of the candidate's experience
counted and at what relevance (with the reason for that relevance), whether
education and wage passed and why, and how the components were weighted
together. A recruiter should never be shown a bare score they can't
interrogate.
"""
from datetime import date

from app.models.vacancy import DEFAULT_SCORING_WEIGHTS
from app.services.scoring.education_matcher import check_education
from app.services.scoring.relevance import compute_relevant_experience, cosine_similarity
from app.services.scoring.skill_matcher import match_skill_list
from app.services.scoring.wage_matcher import check_wage

# Document types that can't meaningfully be scored against a vacancy.
NON_SCORABLE_DOCUMENT_TYPES = {"not_a_resume", "blank_or_unreadable"}


def _to_native(obj):
    """Recursively converts numpy scalar types (e.g. float32) to native Python
    types so results are JSON-serializable when stored in the database."""
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    if hasattr(obj, "item"):  # numpy scalar types (float32, int64, etc.)
        return obj.item()
    return obj


def _weights(vacancy) -> dict:
    """Vacancy weights, backfilled with defaults so a vacancy created under
    an older weight set doesn't break scoring."""
    weights = dict(DEFAULT_SCORING_WEIGHTS)
    for key, value in (vacancy.scoring_weights or {}).items():
        if key in DEFAULT_SCORING_WEIGHTS:  # ignore keys from the retired two-tier model
            weights[key] = value
    return weights


def _zero_score(reason: str, weights: dict, wage_hard_fail: bool = False) -> dict:
    return _to_native({
        "total_score": 0.0,
        "hard_filter_score": 0.0,
        "semantic_score": 0.0,
        "breakdown": {
            "scorable": False,
            "reason": reason,
            "wage_hard_fail": wage_hard_fail,
            "weights_used": weights,
        },
    })


def score_candidate(
    candidate,
    vacancy,
    job_relevances: dict[int, dict] | None = None,
    today: date | None = None,
) -> dict:
    """Scores one candidate against one vacancy.

    `job_relevances` maps work_history index -> {"relevance": float, "reason": str},
    as produced by relevance_judge and cached per (candidate, vacancy).
    Passing None (or an incomplete map) degrades gracefully: any job without
    a judgement counts at full relevance rather than being silently dropped,
    since erasing real experience because of an infrastructure failure is
    the worse error.
    """
    weights = _weights(vacancy)
    job_relevances = job_relevances or {}

    # --- Guard rails ---------------------------------------------------------
    # A candidate can be marked "skipped_wage_veto" at upload time, before any
    # parsing was even attempted (see upload_candidates in candidates.py) - the
    # recruiter already knew the wage expectation was unaffordable, so the AI
    # pipeline was never run at all. This is NOT a parsing failure, so it gets
    # its own message rather than the generic "could not be parsed" one below.
    if candidate.parsing_status == "skipped_wage_veto":
        return _zero_score(
            candidate.parsing_error or "Not processed - wage expectation exceeds the vacancy's budget",
            weights,
            wage_hard_fail=True,
        )

    if candidate.parsing_status != "success" or not candidate.structured_data:
        return _zero_score(
            f"Resume could not be parsed ({candidate.parsing_error or 'unknown error'}), so this "
            "candidate cannot be scored",
            weights,
        )

    if candidate.document_type in NON_SCORABLE_DOCUMENT_TYPES:
        label = (
            "The uploaded file does not appear to be a CV"
            if candidate.document_type == "not_a_resume"
            else "The uploaded file was blank or unreadable"
        )
        return _zero_score(f"{label} - scored 0 without evaluating requirements", weights)

    structured = candidate.structured_data
    jobs = structured.get("work_history") or []

    # --- Skills ----------------------------------------------------------------
    required_score, required_details = match_skill_list(
        vacancy.required_skills or [],
        vacancy.required_skills_canonical,
        structured,
        vacancy.required_skills_type,
        vacancy.required_skills_language_code,
    )
    preferred_score, preferred_details = match_skill_list(
        vacancy.preferred_skills or [],
        vacancy.preferred_skills_canonical,
        structured,
        vacancy.preferred_skills_type,
        vacancy.preferred_skills_language_code,
    )

    # --- Responsibilities match ---------------------------------------------
    # How well the candidate's actual work matches what the role involves.
    # Taken as the best single job's relevance rather than an average, so a
    # strong relevant job isn't dragged down by unrelated earlier work - a
    # career-changer with two years of exactly the right experience should
    # read as a good match despite a long unrelated history.
    relevance_values = [r.get("relevance", 0.0) for r in job_relevances.values()]
    best_job_relevance = max(relevance_values) if relevance_values else None

    if best_job_relevance is not None:
        responsibilities_score = best_job_relevance
        responsibilities_basis = "best-matching job vs the vacancy's responsibilities (AI-judged)"
    elif candidate.embedding is not None and vacancy.responsibilities_embedding is not None:
        # Fallback only. Cosine here is weak (see relevance_judge.py) but is
        # better than nothing when the judgement call was unavailable.
        raw = cosine_similarity(list(candidate.embedding), list(vacancy.responsibilities_embedding))
        responsibilities_score = max(0.0, min(1.0, raw))
        responsibilities_basis = "candidate profile vs responsibilities (embedding fallback - less reliable)"
    else:
        responsibilities_score = 0.0
        responsibilities_basis = "unavailable - no relevance judgement and no embedding"

    # --- Experience (relevance-weighted) ------------------------------------
    relevance_weights = {i: r.get("relevance", 1.0) for i, r in job_relevances.items()}
    relevant_years, experience_audit, experience_is_estimate = compute_relevant_experience(
        jobs, relevance_weights, today=today
    )

    # Attach each judgement's reason to its job in the audit trail.
    for entry in experience_audit:
        judgement = job_relevances.get(entry["job_index"])
        if judgement and judgement.get("reason"):
            entry["relevance_reason"] = judgement["reason"]

    min_required = vacancy.min_experience_years or 0.0
    if min_required <= 0:
        experience_score = 1.0
        experience_reason = "Vacancy specifies no minimum experience"
    else:
        # Partial credit rather than pass/fail: 2 of 3 required years scores 0.67.
        experience_score = min(1.0, relevant_years / min_required)
        experience_reason = f"{relevant_years} relevance-weighted years against a {min_required}-year minimum"

    # --- Education -----------------------------------------------------------
    candidate_fields = {
        "education_level": candidate.education_level,
        "currently_enrolled": candidate.currently_enrolled,
        "current_degree_level": candidate.current_degree_level,
        "expected_graduation_year": candidate.expected_graduation_year,
        "highest_completed_degree_graduation_year": candidate.highest_completed_degree_graduation_year,
    }
    education_result = check_education(structured, candidate_fields, vacancy)
    education_score = 1.0 if education_result["passed"] else 0.0

    # --- Wage ------------------------------------------------------------------
    wage_result = check_wage(candidate.wage_expectation, candidate.wage_currency, vacancy)
    wage_score = wage_result["score"]

    # --- Combine -----------------------------------------------------------------
    components = {
        "responsibilities": (responsibilities_score, weights["responsibilities_weight"]),
        "wage": (wage_score, weights["wage_weight"]),
        "experience": (experience_score, weights["experience_weight"]),
        "required_skills": (required_score, weights["required_skills_weight"]),
        "education": (education_score, weights["education_weight"]),
        "preferred_skills": (preferred_score, weights["preferred_skills_weight"]),
    }
    total_score = round(sum(score * weight for score, weight in components.values()) * 100, 1)

    # Wage hard veto: a candidate wanting >= 2x the budget ceiling is not a
    # realistic hire regardless of fit, so the whole score snaps to 0. The full
    # breakdown is still returned (with wage.hard_fail=True and its reason) so a
    # recruiter can see this was a wage veto, not a poor overall match.
    wage_hard_fail = bool(wage_result.get("hard_fail"))
    if wage_hard_fail:
        total_score = 0.0

    # Kept for the Match table's existing columns and for anyone who wants the
    # rule-based vs role-match split at a glance: "hard filter" is everything
    # except the responsibilities component, renormalised to 0-100.
    rule_based_weight = sum(w for name, (_, w) in components.items() if name != "responsibilities")
    hard_filter_score = (
        round(
            sum(s * w for name, (s, w) in components.items() if name != "responsibilities")
            / rule_based_weight
            * 100,
            1,
        )
        if rule_based_weight
        else 0.0
    )

    return _to_native({
        "total_score": total_score,
        "hard_filter_score": hard_filter_score,
        "semantic_score": round(responsibilities_score * 100, 1),
        "breakdown": {
            "scorable": True,
            "wage_hard_fail": wage_hard_fail,
            "model": "flat weighted sum of six components",
            "weights_used": weights,
            "components": {
                name: {
                    "score": round(score, 3),
                    "weight": weight,
                    "contribution": round(score * weight * 100, 1),
                }
                for name, (score, weight) in components.items()
            },
            "responsibilities": {
                "score": round(responsibilities_score, 3),
                "basis": responsibilities_basis,
            },
            "required_skills": {
                "matched": [d["requirement"] for d in required_details if d.get("matched")],
                "missing": [d["requirement"] for d in required_details if d.get("matched") is False],
                "not_scored_here": [
                    {"requirement": d["requirement"], "reason": d.get("excluded_reason")}
                    for d in required_details
                    if d.get("matched") is None
                ],
                "details": required_details,
            },
            "preferred_skills": {
                "matched": [d["requirement"] for d in preferred_details if d.get("matched")],
                "missing": [d["requirement"] for d in preferred_details if d.get("matched") is False],
                "not_scored_here": [
                    {"requirement": d["requirement"], "reason": d.get("excluded_reason")}
                    for d in preferred_details
                    if d.get("matched") is None
                ],
                "details": preferred_details,
            },
            "experience": {
                "relevant_years": relevant_years,
                "raw_total_years": candidate.total_work_experience_years,
                "min_required": min_required,
                "passed": experience_score >= 1.0,
                "is_estimate": experience_is_estimate,
                "reason": experience_reason,
                "relevance_basis": "AI-judged per job" if job_relevances else "not judged - counted at full relevance",
                "per_job": experience_audit,
            },
            "education": education_result,
            "wage": wage_result,
        },
    })