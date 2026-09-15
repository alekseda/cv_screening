"""
Per-job relevance judgement: how relevant is each of a candidate's jobs to
what this vacancy actually involves?

Why this replaced embedding cosine similarity
---------------------------------------------
Relevance was originally derived from cosine similarity between a job's text
and the vacancy's responsibilities. Real-world testing showed that doesn't
work, for two structural reasons:

1. Cosine similarity between multilingual professional prose sits in a narrow
   band (~0.3-0.8) whether or not the roles actually match, so there's very
   little usable signal to threshold on.
2. It's sensitive to text length, not just meaning. A job with a bare 2-word
   title ("IT TEXNIK") compared against a long responsibilities list scores
   low purely because of the length mismatch. In testing, three jobs with the
   IDENTICAL title scored 0.477 / 0.412 / 0.333 - identical role text cannot
   legitimately produce three different relevance scores, which made clear the
   number was tracking incidental text properties rather than relevance.

Judging relevance is a language-understanding task ("is Helpdesk work relevant
to a Technical Support role?"), which is what the model is actually good at -
unlike summing date ranges, which we deliberately moved OUT of the model and
into Python for the opposite reason.

Cost and caching
----------------
One batched call per candidate (all their jobs at once), not per job. Results
are cached per (candidate, vacancy) so re-scoring after a weight change costs
nothing. At 300-500 candidates per vacancy that's 300-500 cached calls for the
first scoring run and zero thereafter.

The model here is intentionally left on ANTHROPIC_MODEL (the stronger tier),
unlike resume structuring - this docstring's own "why this replaced embedding
cosine similarity" section is exactly the finding that this task needs real
language-understanding, so it's excluded from the cheap-model-by-default
efficiency pass applied to structuring. The system prompt below is static, so
it's now marked cacheable (cache_control) - that part of the cost is still
worth trimming without touching the model doing the judging.

Auditability
------------
Each judgement comes back with a one-line reason, stored and surfaced in the
score breakdown, so a recruiter can see why a job was or wasn't counted.
"""
from typing import Any

from anthropic import Anthropic

from app.core.config import settings

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise RelevanceJudgementError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


class RelevanceJudgementError(Exception):
    pass


RELEVANCE_TOOL = {
    "name": "rate_job_relevance",
    "description": "Rate how relevant each of the candidate's jobs is to the target vacancy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ratings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "job_index": {"type": "integer", "description": "The index number shown next to the job"},
                        "relevance": {
                            "type": "number",
                            "description": "0.0 to 1.0 - how relevant this job's work is to the vacancy",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One short sentence explaining the rating, for a recruiter to read",
                        },
                    },
                    "required": ["job_index", "relevance", "reason"],
                },
            }
        },
        "required": ["ratings"],
    },
}

SYSTEM_PROMPT = """You rate how relevant a candidate's past jobs are to a target vacancy, for an HR \
screening tool. Job titles and descriptions may be in Azerbaijani, Russian, or English.

Rate each job from 0.0 to 1.0 using this scale:
- 1.0  - Same role. The job title or duties are the same role as the vacancy, or a direct equivalent \
under a different name (e.g. "Helpdesk", "IT Support Specialist", "Texniki dəstək mütəxəssisi", \
"Service Desk" are all the same role as a Technical Support Specialist vacancy).
- 0.85-0.95 - Same field, closely adjacent role that clearly builds the same core skills (e.g. System \
Administrator or Network Administrator for a technical support vacancy).
- 0.6-0.8 - Same broad field but a meaningfully different specialisation.
- 0.3-0.5 - Different field, but with genuinely transferable elements.
- 0.0-0.2 - Unrelated work (e.g. warehouse worker, jeweller, hotel gym staff for an IT vacancy).

Important rules:
- Judge by the ROLE and the WORK, not by how much text a job has. A job listed with only a title and no \
description must be rated on that title alone, and must NOT be penalised for being brief. Two jobs with \
the same title and the same duties must get the SAME rating, regardless of the company or how much \
detail was written.
- An internship or trainee position in the right field still counts as that field's work - rate it on the \
role, not the seniority.
- Do not inflate ratings for unrelated work. Administrative, sales, or manual jobs at a technology \
company are still unrelated work if the duties themselves aren't relevant.
- Always respond by calling the rate_job_relevance tool, with exactly one rating per job listed."""


def _format_job(index: int, job: dict) -> str:
    title = (job.get("title") or "").strip() or "(no title given)"
    company = (job.get("company") or "").strip()
    description = (job.get("description") or "").strip()

    lines = [f"[{index}] Title: {title}"]
    if company:
        lines.append(f"    Company: {company}")
    if description:
        snippet = description if len(description) <= 1200 else description[:1200] + "..."
        lines.append(f"    Duties: {snippet}")
    else:
        lines.append("    Duties: (none listed - judge from the title alone)")
    return "\n".join(lines)


def judge_job_relevance(jobs: list[dict], vacancy_title: str, vacancy_responsibilities: str) -> dict[int, dict]:
    """Returns {job_index: {"relevance": float, "reason": str}} for every job.

    Raises RelevanceJudgementError on failure; callers should fall back to a
    neutral relevance rather than dropping the experience entirely.
    """
    if not jobs:
        return {}

    client = _get_client()

    role_text = (vacancy_responsibilities or "").strip() or "(no responsibilities listed)"
    if len(role_text) > 4000:
        role_text = role_text[:4000] + "..."

    job_block = "\n\n".join(_format_job(i, job) for i, job in enumerate(jobs) if isinstance(job, dict))

    user_content = (
        f"TARGET VACANCY\nTitle: {vacancy_title}\n\nWhat the role involves:\n{role_text}\n\n"
        f"CANDIDATE'S JOBS\n{job_block}\n\n"
        f"Rate all {len(jobs)} jobs."
    )

    try:
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=[RELEVANCE_TOOL],
            tool_choice={"type": "tool", "name": "rate_job_relevance"},
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        raise RelevanceJudgementError(f"Claude API call failed: {e}") from e

    ratings: list[Any] | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "rate_job_relevance":
            ratings = block.input.get("ratings")
            break

    if ratings is None:
        raise RelevanceJudgementError("Model response did not include ratings")

    out: dict[int, dict] = {}
    for rating in ratings:
        try:
            index = int(rating["job_index"])
            value = float(rating["relevance"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < len(jobs):
            out[index] = {
                "relevance": max(0.0, min(1.0, value)),
                "reason": str(rating.get("reason") or ""),
            }

    return out
