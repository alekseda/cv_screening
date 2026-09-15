"""
Wage fit: a candidate's single salary expectation against the vacancy's
budget range.

Design decisions worth stating explicitly, since they're deliberate rather
than obvious:
- At or below wage_max scores full credit. Being under budget isn't a problem
  for the employer, so there's no reason to penalise it - and penalising it
  would push the ranking toward more expensive candidates for no reason.
- Above wage_max degrades gradually rather than failing outright. Wage
  expectations are usually negotiable in a way a missing required skill isn't,
  so a candidate 10% over budget shouldn't be treated like a hard fail.
- BUT at or above 2x the budget ceiling, wage is treated as a hard veto: the
  candidate's TOTAL score goes to 0, not just the wage component. A candidate
  wanting double the budget is not a realistic hire regardless of how well
  they match on everything else, and burying that at the bottom of a ranking
  (rather than at zero) would waste a recruiter's attention. The engine reads
  the `hard_fail` flag this sets.
- No range configured, or no expectation recorded, scores neutral (full
  credit). A criterion the recruiter never set up, or data nobody entered,
  shouldn't quietly drag a candidate down - but the breakdown says so plainly
  so the recruiter knows the check didn't actually run.
"""

# How far above wage_max a candidate can be before the component hits zero.
# 1.0 = at double the budget ceiling, wage fit scores 0.
OVER_BUDGET_ZERO_AT = 1.0

# At or above this multiple of the budget ceiling, the whole candidate score is
# vetoed to 0 (see engine's handling of the `hard_fail` flag), not merely the
# wage component. 2.0 = wanting double the budget or more.
HARD_FAIL_MULTIPLE = 2.0


def check_wage(candidate_wage: float | None, candidate_currency: str | None, vacancy) -> dict:
    result: dict = {
        "vacancy_wage_min": vacancy.wage_min,
        "vacancy_wage_max": vacancy.wage_max,
        "vacancy_currency": vacancy.wage_currency,
        "candidate_wage_expectation": candidate_wage,
        "candidate_currency": candidate_currency,
        "score": 1.0,
        "status": "not_applicable",
        "hard_fail": False,
        "reason": "",
    }

    if vacancy.wage_min is None and vacancy.wage_max is None:
        result["reason"] = "No wage range configured for this vacancy - wage not scored"
        return result

    if candidate_wage is None:
        result["status"] = "unknown"
        result["reason"] = "No wage expectation recorded for this candidate - wage not scored"
        return result

    # Currencies are compared, not converted: silently converting with a stale
    # or assumed rate would produce a confidently wrong score. Flag it instead
    # and let the recruiter judge.
    if candidate_currency and vacancy.wage_currency and candidate_currency.upper() != vacancy.wage_currency.upper():
        result["status"] = "currency_mismatch"
        result["reason"] = (
            f"Candidate expectation is in {candidate_currency} but the vacancy budget is in "
            f"{vacancy.wage_currency} - not comparable without a conversion rate, so wage was not scored"
        )
        return result

    ceiling = vacancy.wage_max if vacancy.wage_max is not None else vacancy.wage_min

    if candidate_wage <= ceiling:
        result["status"] = "within_budget"
        result["score"] = 1.0
        result["reason"] = f"Expectation {candidate_wage} is within the budget (ceiling {ceiling})"
        return result

    # Hard veto: at or above 2x the ceiling, zero out the whole candidate.
    if ceiling and candidate_wage >= ceiling * HARD_FAIL_MULTIPLE:
        result["status"] = "over_budget"
        result["score"] = 0.0
        result["hard_fail"] = True
        result["reason"] = (
            f"Expectation {candidate_wage} is at least {HARD_FAIL_MULTIPLE:g}x the budget ceiling "
            f"{ceiling} - candidate scored 0 overall as an unrealistic wage mismatch"
        )
        return result

    excess_ratio = (candidate_wage - ceiling) / ceiling if ceiling else 1.0
    score = max(0.0, 1.0 - (excess_ratio / OVER_BUDGET_ZERO_AT))

    result["status"] = "over_budget"
    result["score"] = round(score, 3)
    result["reason"] = (
        f"Expectation {candidate_wage} exceeds the budget ceiling {ceiling} "
        f"by {round(excess_ratio * 100)}%"
    )
    return result
