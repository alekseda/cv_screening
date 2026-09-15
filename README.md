# CV Screening Tool — Phase 1-2: Scaffold + Auth + Resume Parsing

Multi-user AI resume-matching tool.
- **Phase 1**: project skeleton, database, JWT-based recruiter login.
- **Phase 2** (this update): vacancy CRUD (manual JD entry) + resume upload +
  the parsing pipeline (PDF/DOCX extraction, OCR fallback, Claude structuring).

Embeddings and the scoring engine are built in Phases 3-4.

## Stack
- **Backend**: FastAPI (async), SQLAlchemy 2.0, PostgreSQL + pgvector, Alembic
- **Auth**: JWT (python-jose) + bcrypt password hashing
- **Parsing**: PyMuPDF (PDF text), python-docx (DOCX text), Tesseract OCR
  (aze+rus+eng) for scanned pages, Claude (forced tool-use) for structuring
- Embedding column (`Vector(1024)`) is already in the schema, ready for Voyage AI in Phase 3

## Run it — option A: fully via Docker (recommended now that OCR needs system packages)

```bash
cd backend
cp .env.example .env
# Edit .env: set a real JWT_SECRET_KEY and your ANTHROPIC_API_KEY
python -c "import secrets; print(secrets.token_urlsafe(64))"

cd ..
docker compose up -d --build
docker compose exec api alembic upgrade head
```

Visit **http://localhost:8000/docs**. Both Postgres+pgvector and the app
(with Tesseract's aze/rus/eng language packs baked into its image via
`backend/Dockerfile`) are running in containers.

## Run it — option B: app on host, only Postgres in Docker

Use this if you want faster reload cycles while developing.

### 1. Start the database only
```bash
docker compose up -d db
```

### 2. Install Tesseract + language packs on your host
- **Ubuntu/Debian**: `sudo apt install tesseract-ocr tesseract-ocr-aze tesseract-ocr-rus tesseract-ocr-eng`
- **macOS**: `brew install tesseract tesseract-lang` (includes aze/rus/eng)
- **Windows**: install via the [UB-Mannheim Tesseract installer](https://github.com/UB-Mannheim/tesseract/wiki), ticking Azerbaijani and Russian in the "Additional language data" step, then add the install dir to your `PATH`.

Verify: `tesseract --list-langs` should list `aze`, `rus`, `eng`.

### 3. Set up the backend
```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — set a real JWT_SECRET_KEY and your ANTHROPIC_API_KEY
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### 4. Run migrations
```bash
alembic upgrade head
```

### 5. Start the API
```bash
uvicorn app.main:app --reload --port 8000
```

Visit **http://localhost:8000/docs**.

## Try it out
```bash
# Register a recruiter
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"recruiter@company.az","password":"password123","full_name":"Aysel Mammadova"}'

# Log in (OAuth2 form, not JSON)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=recruiter@company.az&password=password123"
# -> returns {"access_token": "...", "token_type": "bearer"}

# Use the token
curl http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer <token>"
```

## Try Phase 2: create a vacancy and upload resumes

The easiest way is via the Swagger UI at `/docs` — click "Authorize" and
paste your token, then use the endpoints interactively. Or via curl:

```bash
TOKEN="<paste access_token here>"

# 1. Create a vacancy (manual JD entry)
curl -X POST http://localhost:8000/api/v1/vacancies \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "title": "Backend Engineer",
    "description": "We are looking for a backend engineer with strong Python experience...",
    "required_skills": ["Python", "FastAPI", "PostgreSQL"],
    "preferred_skills": ["Docker", "AWS"],
    "min_experience_years": 3,
    "education_requirement": "bachelor"
  }'
# -> note the returned "id" as VACANCY_ID

# 2. Upload resumes (PDF/DOCX) against that vacancy
curl -X POST http://localhost:8000/api/v1/vacancies/$VACANCY_ID/candidates \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@/path/to/resume1.pdf" \
  -F "files=@/path/to/resume2.docx"
# -> returns per-file status: success / failed / rejected

# 3. List candidates for the vacancy
curl http://localhost:8000/api/v1/vacancies/$VACANCY_ID/candidates \
  -H "Authorization: Bearer $TOKEN"

# 4. Inspect one candidate's full structured data + raw extracted text
curl http://localhost:8000/api/v1/candidates/$CANDIDATE_ID \
  -H "Authorization: Bearer $TOKEN"
```

A few things worth checking as you test:
- Try at least one **scanned/image-only PDF** to confirm the OCR fallback fires
  (check `detected_language` and that `raw_text` isn't empty).
- Try one resume in each of **Azerbaijani, Russian, and English** and confirm
  `detected_language` and `estimated_total_experience_years` look right.
- If `ANTHROPIC_API_KEY` is missing or invalid, uploads will still succeed at
  the HTTP level but `parsing_status` will be `"failed"` with the error message
  in `parsing_error` — check there before assuming something's broken elsewhere.

## What's in the schema already (for Phases 2-4, not wired up yet)
- `Vacancy.scoring_weights` — configurable per-vacancy weights (hard-filter vs
  semantic split, and sub-weights for required/preferred skills, experience,
  education). Defaults live in `app/models/vacancy.py::DEFAULT_SCORING_WEIGHTS`.
- `Candidate.raw_text` + `Candidate.structured_data` — both stored, so resumes
  can be re-parsed later without re-uploading if the extraction schema changes.
- `Vacancy.embedding` / `Candidate.embedding` — `Vector(1024)` columns
  (matches `voyage-multilingual-2`'s output dimension), unused until Phase 3.
- `Match.score_breakdown` — JSON field that Phase 4's scoring engine fills with
  matched/missing skills, experience/education pass-fail, and the semantic vs.
  hard-filter split, per the auditability requirement.

## Project layout
```
backend/
  app/
    core/               # config, JWT + password hashing
    db/                 # SQLAlchemy engine/session, declarative base
    models/             # User, Vacancy, Candidate, Match
    schemas/            # Pydantic request/response models
    api/routes/         # auth, users, vacancies, candidates  (matching lands in Phase 4)
    services/parsing/    # text_extraction.py, ai_structurer.py, pipeline.py
    main.py
  alembic/              # migrations
  Dockerfile            # app image w/ Tesseract + aze/rus/eng language packs
docker-compose.yml      # Postgres+pgvector + the app
```

## Phase 3 (this update): Embeddings

Voyage AI (`voyage-multilingual-2`) embeddings for both vacancies and
candidates, wired into the flows that already exist:

- Vacancy embeddings are computed on create, and recomputed on update only
  if an embedding-relevant field changes (title/description/skills/
  experience/education) - editing just the scoring weights won't trigger
  an unnecessary API call.
- Candidate embeddings are computed as part of the existing parse pipeline
  (both initial upload and `/reparse`), built from skills, work history,
  volunteer experience, education, certificates, and languages - explicitly
  *not* from name/contact/age/height/military service, which aren't
  relevant to job fit.
- If a Voyage call fails (e.g. `VOYAGE_API_KEY` not set), the vacancy/candidate
  is still created/parsed successfully - the failure is recorded in
  `embedding_error` rather than blocking the request. Retry with
  `POST /vacancies/{id}/recompute-embedding` or `/candidates/{id}/reparse`.
- **Preview endpoint**: `GET /vacancies/{id}/candidates/by-similarity` ranks
  that vacancy's candidates by cosine similarity alone (via pgvector). This
  is *not* the final Phase 4 score - it has no hard filters yet - it's here
  so you can sanity-check that embeddings are actually separating relevant
  from irrelevant resumes before Phase 4 builds the full scoring engine on
  top of it.

Run migration `0005` to add the new `embedding_error` columns, and set
`VOYAGE_API_KEY` in `.env`.

## Phase 4 (this update): Scoring engine

Every candidate gets one 0-100 score per vacancy, blended from a rule-based
half and a semantic half, and always accompanied by a breakdown explaining it.

**Endpoints**
- `POST /vacancies/{id}/score` — (re)scores all candidates for a vacancy.
  Re-runnable and cheap: it reuses stored structured data and embeddings, so
  after tuning `scoring_weights` you just call it again.
- `GET /vacancies/{id}/ranking` — candidates ordered by score, each with the
  full breakdown attached.
- `POST /vacancies/{id}/candidates/{cid}/score` — rescore one candidate.
- `GET /vacancies/{id}/candidates/{cid}/match` — one stored score + breakdown.
- `PATCH /candidates/{id}/wage` — record a salary expectation (manual for now).

**Hard filter (~57.5%)** — fully rule-based and auditable. Sub-weights are
per-vacancy configurable and validated to sum to 1.0:
| Component | Default | Behaviour |
|---|---|---|
| required skills | 0.45 | fraction matched; each match reports the tokens and resume text that satisfied it |
| preferred skills | 0.15 | bonus, deliberately lighter than required |
| experience | 0.20 | partial credit vs. the minimum (2 of 3 years = 0.67) |
| education | 0.10 | pass/fail, with a written reason |
| wage | 0.10 | full credit at or under budget, gradual falloff above |

**Semantic (~42.5%)** — cosine similarity between the candidate profile
embedding and the vacancy embedding. If either embedding is missing, the score
falls back to hard filters alone and says so, rather than treating an
infrastructure failure as zero similarity.

**Relevance-weighted experience.** Each job's duration is multiplied by how
relevant it is, so ten years in an unrelated field no longer sails past a
three-year requirement — but nothing is erased either. Relevance comes from
comparing each job's *description* (falling back to its title) against the
vacancy's *responsibilities*, not title-to-title. Similarity at or above
`ADJACENT_FIELD_THRESHOLD` (0.5) is floored at `ADJACENT_FIELD_FLOOR` (0.7),
so same-field work (network admin → sysadmin) counts heavily. **These two
constants are starting points and should be calibrated against real vacancy/CV
pairs** — see `app/services/scoring/relevance.py`.

Concurrent jobs are never double-counted: overlapping periods are split into
disjoint segments and credited at the highest relevance covering each one.

**Education, two modes.** Standard postings use the usual ladder (a higher
degree satisfies a lower requirement). Postings with `accepts_current_students`
(internships, graduate programmes) instead require being *currently enrolled*
at that level, or having completed exactly that level inside the
graduation-year window — an unrelated master's from years ago does **not**
auto-qualify. Degree-specific graduation years are read from the raw
`education` array, since `highest_completed_degree_graduation_year` answers a
different question for anyone holding more than one degree.

**Non-CV uploads score 0.** Resume parsing now classifies `document_type`
(`resume` / `not_a_resume` / `blank_or_unreadable`); anything that isn't a
resume short-circuits to 0 with an explicit reason rather than being silently
scored against requirements.

Run migration `0008`, then re-parse existing candidates
(`POST /candidates/{id}/reparse`) so they pick up `document_type` and per-job
embeddings, and recompute vacancy embeddings
(`POST /vacancies/{id}/recompute-embedding`) for the new responsibilities vector.

`backend/tests/test_scoring_scenarios.py` runs the engine through six
scenarios (strong match, over-qualified master's, final-year student, non-CV
upload, over-budget, missing embeddings) with no DB or API keys required.

## Cross-language skill matching (fix)

The hard-filter skill matcher is rule-based (auditable) rather than semantic,
which meant a requirement written in Azerbaijani ("vizuallaşdırma") scored
zero against an equivalent English candidate skill ("Data visualization") -
no shared tokens, no match, even though they mean the same thing.

Fixed with a shared canonicalization step (`app/services/parsing/canonicalizer.py`):
one small Claude call turns a list of short phrases into English canonical
forms, in the same order. It's used on both sides:
- **Vacancy**: `required_skills_canonical` / `preferred_skills_canonical`,
  computed whenever `required_skills`/`preferred_skills` are set - whether
  from JD auto-extraction or manual entry.
- **Candidate**: `skills_canonical`, computed at parse time and stored
  alongside `skills` inside `structured_data`.

The matcher then scores a requirement's *original* wording and its
*canonical* form **separately** and takes the better of the two - not their
token union. Unioning was tried first and found to be a bug: it inflates the
required-token count whenever the two phrasings share no literal words (the
normal case across languages), making the match threshold *harder* to clear,
not easier. Verified against the exact reported case
(`vizuallaşdırma` → `data visualization`): union scored it 0.67, take-the-best
scores it a clean 1.0.

Canonicalization is best-effort - a failure falls back to the original
phrases (matching still works, just without the cross-language benefit)
rather than blocking vacancy creation or resume parsing.

Run migration `0009`, then re-run `/vacancies/{id}/score` - existing vacancies
won't have canonical forms until their `required_skills`/`preferred_skills`
are next set, and existing candidates need `/reparse` to get `skills_canonical`.

## Wage veto (this update)

A candidate wanting **2x or more** of the vacancy's budget ceiling now scores
**0 overall**, not just a low wage sub-score - a wage mismatch that large isn't
a realistic hire regardless of how well they match everything else, so it's
treated as a hard veto rather than being buried at the bottom of a ranking.
`wage_matcher.py`'s `HARD_FAIL_MULTIPLE` controls the threshold (default 2.0).
The breakdown still shows the full component scores plus `wage_hard_fail: true`
and the reason, so a recruiter sees this was a wage veto, not a poor overall fit.

## Phase 5: Frontend

A React (Vite) single-page app - login, vacancy creation from a pasted JD,
drag-and-drop résumé upload, and the ranked candidate list with an expandable
per-candidate score breakdown.

**Design**: light, warm-paper background with deep-navy authority color and a
restrained gold-bronze accent for scores; Fraunces (serif) for headings/scores,
Inter for UI - built for calm, confident executive review, not a generic
AI-tool look.

### Run it

```bash
cd frontend
npm install
npm run dev
```

Visit **http://localhost:5173**. The dev server proxies `/api` to the backend
on `:8000` (see `vite.config.js`), so make sure the backend is running first.

For production, `npm run build` outputs static files in `frontend/dist/` -
serve these with any static host (nginx, Caddy, etc.) alongside the API.

### What it does

1. **Sign in / register** - JWT stored in `sessionStorage`.
2. **Vacancies list** → **+ New vacancy**: paste the raw job description
   (optionally override the title or set a wage budget range) and submit -
   everything else is auto-extracted server-side.
3. Open a vacancy → **drag/drop or choose one or more PDF/DOCX files** →
   **Process N résumés**. This uploads and parses each file, then triggers
   scoring for the whole vacancy in one action.
4. The **ranked candidate list** appears - name, score out of 100, and quick
   metadata (experience, education, language). Click a row to expand the full
   breakdown: component-by-component contribution, matched/missing skills,
   per-job relevance with the AI's reasoning, education pass/fail, and wage fit.
   A wage-vetoed candidate shows a red 0 with the veto reason up top.

### Known simplification

Candidate wage expectations aren't entered from the UI yet - the backend
endpoint (`PATCH /candidates/{id}/wage`) exists, but there's no form for it in
this pass. Worth adding to the candidate row (perhaps inline in the expanded
breakdown) in a follow-up.

## Next: Phase 5
Frontend — login, vacancy setup with weight tuning, batch resume upload, and
the ranked candidate list with an expandable per-candidate score breakdown.
