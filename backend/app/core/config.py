"""
Central configuration. All values are overridable via environment variables
or a .env file (see .env.example). Nothing here is hardcoded for production
secrets - this file only defines defaults for local development.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App ---
    PROJECT_NAME: str = "CV Screening Tool"
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    # Local dev default matches docker-compose.yml. Change via .env for other envs.
    DATABASE_URL: str = (
        "postgresql+asyncpg://cvscreen:cvscreen@localhost:5432/cvscreen"
    )

    # --- Auth / JWT ---
    JWT_SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"  # override in .env, always
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12 hours - recruiters keep sessions open

    # --- External AI providers ---
    ANTHROPIC_API_KEY: str | None = None
    # ANTHROPIC_MODEL is the high-accuracy tier: used for job-relevance
    # judgement (relevance_judge.py) and the skill semantic fallback
    # (skill_relevance_judge.py), which the code's own analysis found need
    # real language-understanding, not a cost-cut - see those modules'
    # docstrings for why cosine similarity was tried and rejected there.
    # Was Opus; moved to Sonnet after a side-by-side comparison on real
    # candidates (see git history) found Sonnet's judgements close enough -
    # sometimes stricter in ways that looked more correct, not less - at a
    # fraction of the cost. Revisit ANTHROPIC_MODEL_STRUCTURING_DEFAULT's
    # escalation target too if this changes again.
    ANTHROPIC_MODEL: str = "claude-sonnet-5"
    # Resume STRUCTURING (ai_structurer.py) is a more mechanical extraction
    # task than relevance judgement, so it runs on a cheaper default model
    # first, with automatic escalation to ANTHROPIC_MODEL only when the
    # result looks unreliable (schema failure, suspiciously empty output on
    # substantial input, etc. - see ai_structurer.py). The escalation tier is
    # a success-rate safety net and must not be removed to save cost.
    ANTHROPIC_MODEL_STRUCTURING_DEFAULT: str = "claude-haiku-4-5-20251001"
    VOYAGE_API_KEY: str | None = None
    EMBEDDING_MODEL: str = "voyage-multilingual-2"
    EMBEDDING_DIM: int = 1024  # voyage-multilingual-2 output dimension

    # --- Glorri.az integration (candidate import) ---
    # Static bearer token issued by Glorri for this company's integration -
    # see services/integrations/glorri_client.py. No per-request auth flow;
    # the same token is sent on every call.
    GLORRI_API_BASE_URL: str = "https://api.glorri.com"
    GLORRI_API_TOKEN: str | None = None

    # --- Resume parsing (Phase 2) ---
    OCR_LANGUAGES: str = "aze+rus+eng"  # Tesseract language codes
    MAX_RESUME_TEXT_CHARS: int = 15000  # truncate before sending to the AI structurer

    # --- Concurrency (efficiency pass) ---
    # Bulk uploads process this many files' extract/structure/embed pipeline
    # concurrently (worker threads, bounded by a semaphore) instead of one at
    # a time. Raise if the Anthropic/Voyage rate limits on your account allow
    # more headroom; lower if you see 429s during large batch uploads.
    MAX_CONCURRENT_PARSES: int = 8
    # Same idea for scoring: POST /vacancies/{id}/score runs each candidate's
    # (blocking) job-relevance and skill-relevance judgement calls concurrently,
    # bounded by this semaphore, instead of one candidate at a time - see
    # score_vacancy_candidates in api/routes/matching.py.
    MAX_CONCURRENT_SCORING_CALLS: int = 8

    # --- File storage (used from Phase 2 onward) ---
    RESUME_STORAGE_DIR: str = "./storage/resumes"

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]


settings = Settings()
