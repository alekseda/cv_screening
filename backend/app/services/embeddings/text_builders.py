"""
Turns a Vacancy or a candidate's structured_data into a single text blob for
embedding. Keeping this in one place means the vacancy side and candidate
side stay symmetric - both describe "what this job needs" / "what this
candidate offers" in a similar shape, which matters for cosine similarity
to be meaningful.
"""
from app.models.vacancy import Vacancy


def build_vacancy_embedding_text(vacancy: Vacancy) -> str:
    parts = [f"Job title: {vacancy.title}", vacancy.description]

    if vacancy.responsibilities:
        parts.append("Responsibilities: " + vacancy.responsibilities)
    if vacancy.required_skills:
        parts.append("Required skills: " + ", ".join(vacancy.required_skills))
    if vacancy.preferred_skills:
        parts.append("Preferred/nice-to-have skills: " + ", ".join(vacancy.preferred_skills))
    if vacancy.min_experience_years:
        parts.append(f"Minimum experience: {vacancy.min_experience_years} years")
    if vacancy.education_requirement and vacancy.education_requirement != "none":
        parts.append(f"Education requirement: {vacancy.education_requirement}")
    if vacancy.accepts_current_students:
        note = "Also accepts final-year students who haven't graduated yet"
        if vacancy.graduation_year_min or vacancy.graduation_year_max:
            note += f" or recent graduates ({vacancy.graduation_year_min or '?'}-{vacancy.graduation_year_max or '?'})"
        parts.append(note)

    return "\n\n".join(parts)


def build_job_embedding_text(job: dict) -> str:
    """Text for a single work_history entry's embedding, used for per-job
    relevance weighting against the vacancy's responsibilities.

    Prefers the job's description - what the person actually did is the
    meaningful thing to compare against what a role involves. Falls back to
    the title alone when there's no description, since a title is weak signal
    but far better than nothing. Includes the title alongside the description
    when both exist, as the title adds useful role context.
    """
    title = (job.get("title") or "").strip()
    description = (job.get("description") or "").strip()

    if description and title:
        return f"{title}. {description}"
    if description:
        return description
    return title


def build_vacancy_responsibilities_text(vacancy) -> str:
    """Text for the vacancy's responsibilities-only embedding.

    Falls back to the full description when a vacancy has no separate
    responsibilities section - not every posting will have one extracted,
    and some comparison basis is better than none.
    """
    if vacancy.responsibilities and vacancy.responsibilities.strip():
        parts = [f"Job title: {vacancy.title}", vacancy.responsibilities]
        return "\n\n".join(parts)
    return f"Job title: {vacancy.title}\n\n{vacancy.description}"


def build_candidate_embedding_text(structured: dict) -> str:
    """Builds an embedding text from AI-structured resume data. Deliberately
    excludes full_name/contact/age/height/military_service - those aren't
    semantically relevant to job fit and shouldn't influence similarity."""
    parts: list[str] = []

    skills = structured.get("skills") or []
    if skills:
        parts.append("Skills: " + ", ".join(skills))

    for job in structured.get("work_history") or []:
        title = job.get("title") or ""
        company = job.get("company") or ""
        description = job.get("description") or ""
        line = f"{title} at {company}. {description}".strip()
        if line.strip("."):
            parts.append(line)

    for vol in structured.get("volunteer_experience") or []:
        role = vol.get("role") or "Volunteer"
        org = vol.get("organization") or ""
        description = vol.get("description") or ""
        line = f"Volunteer - {role} at {org}. {description}".strip()
        if line.strip("."):
            parts.append(line)

    for edu in structured.get("education") or []:
        institution = edu.get("institution") or ""
        degree = edu.get("degree") or ""
        field = edu.get("field_of_study") or ""
        line = f"Education: {degree} in {field}, {institution}".strip()
        parts.append(line)

    certificates = structured.get("certificates") or []
    if certificates:
        parts.append("Certificates: " + ", ".join(c.get("name", "") for c in certificates if c.get("name")))

    languages = structured.get("languages_spoken") or []
    if languages:
        lang_strs = [
            f"{lang.get('language')} ({lang.get('proficiency')})" if lang.get("proficiency") else lang.get("language")
            for lang in languages
            if lang.get("language")
        ]
        if lang_strs:
            parts.append("Languages: " + ", ".join(lang_strs))

    total_years = structured.get("estimated_total_work_experience_years")
    if total_years is not None:
        parts.append(f"Total years of work experience: {total_years}")

    if structured.get("currently_enrolled"):
        degree = structured.get("current_degree_level") or "a degree"
        grad_year = structured.get("expected_graduation_year")
        line = f"Currently enrolled, pursuing {degree}"
        if grad_year:
            line += f", expected graduation {grad_year}"
        parts.append(line)

    return "\n".join(p for p in parts if p and p.strip())
