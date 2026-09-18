"""
Computes total experience duration from a list of {start_date, end_date,
is_current} entries - deterministically in Python, NOT by asking the AI to
sum durations itself.

Why: Claude reliably extracts individual start_date/end_date values from a
resume (that's a text-understanding task it's good at), but summing several
date ranges - especially with overlap-merging - is multi-step arithmetic done
"in the model's head," which is a well-known weak spot for any LLM. Moving
the actual math into deterministic code makes the total exactly reproducible
and auditable instead of a black-box AI output that occasionally sums wrong.
"""
from datetime import date, datetime, timedelta

# Average days/year used for the day-count -> years conversion, accounting for leap years.
DAYS_PER_YEAR = 365.25


def parse_partial_date(value: str | None) -> date | None:
    """Parses YYYY-MM-DD, YYYY-MM, or YYYY (as produced by the AI extractor)
    into a date, using the 1st of the month/year for missing parts. Returns
    None if the value is missing or doesn't match any of these formats."""
    if not value or not isinstance(value, str):
        return None

    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            return date(parsed.year, parsed.month, parsed.day)
        except ValueError:
            continue
    return None


def _has_month_precision(value: str | None) -> bool:
    """True if `value` includes a month (YYYY-MM or YYYY-MM-DD) rather than
    just a bare year - used to decide whether a stated duration should
    override dates computed from `value`. A "-" is present in both
    month-precision formats and absent from a bare "YYYY", so this is enough
    to distinguish them without re-parsing."""
    return bool(value) and isinstance(value, str) and "-" in value.strip()


def resolve_job_period(entry: dict, today: date) -> tuple[date, date] | None:
    """Resolves one work_history/volunteer_experience entry to a (start, end)
    interval, or None if it can't be resolved at all (no usable start date,
    or a malformed range with end before start).

    end is `today` if the role is current or has no end_date - always
    computed fresh, never from a stated duration, since a stated duration on
    an ongoing role goes stale the moment it was written down.

    For a COMPLETED role, prefers an explicitly stated duration
    (stated_duration_months) over the parsed end_date when the dates
    themselves are only year-precision: subtracting two bare years is a poor
    estimate for anything shorter than ~a year (e.g. a 3-month stint entirely
    within one calendar year comes out as 0 days; one spanning a year
    boundary comes out as a full year instead of ~3 months), while a stated
    duration is a fixed, precise fact about how long the role actually ran.
    Falls back to the parsed end_date when no stated duration is available,
    or when the dates already have month precision (in which case they're
    the more reliable source - see ai_structurer.py's field description).
    """
    start = parse_partial_date(entry.get("start_date"))
    if start is None:
        return None

    if entry.get("is_current") or not entry.get("end_date"):
        return start, today

    stated_months = entry.get("stated_duration_months")
    has_month_precision = _has_month_precision(entry.get("start_date")) and _has_month_precision(entry.get("end_date"))

    if stated_months is not None and not has_month_precision:
        try:
            months = float(stated_months)
        except (TypeError, ValueError):
            months = None
        if months is not None and months >= 0:
            end = start + timedelta(days=months * DAYS_PER_YEAR / 12)
            return (start, end) if end >= start else None

    end = parse_partial_date(entry.get("end_date")) or today
    return (start, end) if end >= start else None


def compute_total_experience(entries: list[dict], today: date | None = None) -> tuple[float | None, bool]:
    """Computes total duration across a list of work_history/volunteer_experience
    entries, merging overlapping periods so concurrent roles aren't double-counted.

    Returns (total_years, is_estimate):
    - No entries at all -> (0.0, False) - genuinely zero experience, not unknown.
    - Entries exist but NONE have a parseable start_date -> (None, False) - truly
      unknown, nothing to compute from.
    - Entries exist and SOME are computable -> (sum of computable ones, True) -
      a real number, but flagged as an estimate since it excludes undated entries.
    - Entries exist and ALL are computable -> (sum, False) - a complete, non-estimated total.
    """
    if not entries:
        return 0.0, False

    today = today or date.today()

    intervals: list[tuple[date, date]] = []
    skipped_count = 0

    for entry in entries:
        period = resolve_job_period(entry, today)
        if period is None:
            skipped_count += 1
            continue
        intervals.append(period)

    if not intervals:
        return None, False

    intervals.sort()
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    total_days = sum((end - start).days for start, end in merged)
    total_years = round(total_days / DAYS_PER_YEAR, 1)
    is_estimate = skipped_count > 0

    return total_years, is_estimate
