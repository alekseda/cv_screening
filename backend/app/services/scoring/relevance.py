"""
Relevance-weighted experience.

Plain total experience is a poor signal on its own: ten years in an unrelated
field would sail past a three-year requirement. But zeroing out non-matching
experience is equally wrong - a year in IT support is genuinely worth
something toward a sysadmin role, just not as much as a year of sysadmin work.

So each job's duration is multiplied by a relevance weight in [0, 1], derived
from cosine similarity between that job's embedding and the vacancy's
*responsibilities* embedding. Responsibilities rather than the whole posting
because what a role involves day-to-day is the meaningful comparison; and the
job's *description* rather than its title, because titles are unreliable (two
"Manager" roles can be completely different jobs). Titles are used only as a
fallback when a job has no description.

Overlapping periods are handled by splitting time into disjoint segments and
taking the highest relevance among the jobs covering each segment - so
concurrent roles are never double-counted, and holding a relevant job
alongside an irrelevant one credits the relevant one.
"""
from datetime import date

from app.services.parsing.experience_calculator import DAYS_PER_YEAR, parse_partial_date

# Above this similarity, two roles are treated as being in the same broad field
# (e.g. network admin vs sysadmin) and get floored at ADJACENT_FIELD_FLOOR.
#
# NOTE: these are starting points, not settled constants. Cosine similarity
# between professional-prose texts clusters in a narrower band than people
# expect (shared workplace vocabulary keeps even unrelated pairs off zero), so
# they should be calibrated against real vacancy/CV pairs once there's data.
# They're exposed in the score breakdown precisely so that tuning is possible.
ADJACENT_FIELD_THRESHOLD = 0.5
ADJACENT_FIELD_FLOOR = 0.7


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity. Computed in Python rather than in the database
    because scoring already holds both vectors in memory at this point."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def relevance_from_similarity(similarity: float) -> float:
    """Maps raw similarity to a relevance weight, with a floor for
    same-field-adjacent work. Clamped to [0, 1] - cosine can go negative,
    which would otherwise subtract experience."""
    similarity = max(0.0, min(1.0, similarity))
    if similarity >= ADJACENT_FIELD_THRESHOLD:
        return max(similarity, ADJACENT_FIELD_FLOOR)
    return similarity


def compute_relevant_experience(
    jobs: list[dict],
    job_relevances: dict[int, float],
    today: date | None = None,
) -> tuple[float, list[dict], bool]:
    """Computes relevance-weighted total experience in years.

    `job_relevances` maps a job's index in `jobs` to its relevance weight.
    Jobs with no entry default to 1.0 - if relevance couldn't be computed
    (e.g. the embedding failed), it's better to count the experience at face
    value than to silently erase it.

    Returns (weighted_years, per_job_audit, is_estimate). is_estimate is True
    when at least one job had to be skipped for want of usable dates.
    """
    today = today or date.today()

    intervals: list[tuple[date, date, float, int]] = []
    audit: list[dict] = []
    skipped = 0

    for index, job in enumerate(jobs):
        relevance = job_relevances.get(index, 1.0)
        start = parse_partial_date(job.get("start_date"))

        entry = {
            "job_index": index,
            "title": job.get("title"),
            "company": job.get("company"),
            "start_date": job.get("start_date"),
            "end_date": job.get("end_date"),
            "is_current": bool(job.get("is_current")),
            "relevance": round(relevance, 3),
            "counted": False,
            "raw_years": None,
            "weighted_years": None,
        }

        if start is None:
            skipped += 1
            entry["note"] = "No usable start date - excluded from the total"
            audit.append(entry)
            continue

        end = today if (job.get("is_current") or not job.get("end_date")) else (
            parse_partial_date(job.get("end_date")) or today
        )
        if end < start:
            skipped += 1
            entry["note"] = "End date precedes start date - excluded from the total"
            audit.append(entry)
            continue

        raw_years = round((end - start).days / DAYS_PER_YEAR, 2)
        entry.update(
            counted=True,
            raw_years=raw_years,
            weighted_years=round(raw_years * relevance, 2),
        )
        audit.append(entry)
        intervals.append((start, end, relevance, index))

    if not intervals:
        return 0.0, audit, skipped > 0

    # Split the timeline into disjoint segments at every interval boundary, and
    # credit each segment at the highest relevance among the jobs covering it.
    # This is what stops two concurrent jobs being counted twice, while making
    # sure a relevant job isn't diluted by an irrelevant one running alongside it.
    boundaries = sorted({d for start, end, _, _ in intervals for d in (start, end)})
    total_weighted_days = 0.0

    for segment_start, segment_end in zip(boundaries, boundaries[1:]):
        covering = [rel for start, end, rel, _ in intervals if start <= segment_start and end >= segment_end]
        if not covering:
            continue
        segment_days = (segment_end - segment_start).days
        total_weighted_days += segment_days * max(covering)

    return round(total_weighted_days / DAYS_PER_YEAR, 2), audit, skipped > 0
