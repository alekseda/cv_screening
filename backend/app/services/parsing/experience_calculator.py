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
from datetime import date, datetime

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
        start = parse_partial_date(entry.get("start_date"))
        if start is None:
            skipped_count += 1
            continue

        if entry.get("is_current") or not entry.get("end_date"):
            end = today
        else:
            end = parse_partial_date(entry.get("end_date")) or today

        if end < start:
            # Malformed dates (end before start) - can't trust this entry either.
            skipped_count += 1
            continue

        intervals.append((start, end))

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
