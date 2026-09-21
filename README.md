# CV Screening Tool

Multi-user AI resume-matching tool for recruiters: paste a job description
(or enter it manually), upload a batch of résumés (PDF/DOCX, any mix of
Azerbaijani/Russian/English, scanned or text), and get every candidate ranked
0-100 against that vacancy with a full, auditable breakdown of *why*.

## Stack
- **Backend**: FastAPI (async), SQLAlchemy 2.0, PostgreSQL + pgvector, Alembic
- **Frontend**: React (Vite)
- **Auth**: JWT (python-jose) + bcrypt password hashing
- **Parsing**: PyMuPDF (PDF text, position-ordered for multi-column layouts),
  python-docx (DOCX text), Tesseract OCR (aze+rus+eng) for scanned pages,
  Claude (forced tool-use) for structuring into a fixed schema
- **Embeddings**: Voyage AI (`voyage-multilingual-2`, 1024-dim) for
  vacancy/candidate profile similarity
- **AI judgement calls**: Claude, used where the code found rule-based or
  embedding approaches genuinely don't work (see "How scoring works" below)

### Model tiering (cost control)
Two Anthropic model settings, not one - because not every AI call in this
system needs the same accuracy/cost tradeoff:
- `ANTHROPIC_MODEL_STRUCTURING_DEFAULT` (default: a Haiku tier) - resume
  structuring, a mostly-mechanical extraction task. Auto-escalates to
  `ANTHROPIC_MODEL` if the cheap model's output looks unreliable (schema
  failure, suspiciously empty output on substantial input).
- `ANTHROPIC_MODEL` (default: a Sonnet tier) - job-relevance judgement
  (`relevance_judge.py`) and the skill semantic fallback
  (`skill_relevance_judge.py`). Both were deliberately kept off rule-based/
  embedding shortcuts after testing showed those don't work for these
  specific tasks - see each module's own docstring for what was tried and
  why it failed. Was Opus originally; moved to Sonnet after a head-to-head
  comparison on real candidates found Sonnet's judgements close enough -
  sometimes stricter in ways that looked more correct, not less - at a
  fraction of the cost.

Both are plain env vars (see `.env.example`), so swapping in a different
provider/model for either tier is a config change, not a code change -
useful context if you're benchmarking cheaper models for either tier.

## Run it — option A: fully via Docker (recommended, since OCR needs system packages)

```bash
cd backend
cp .env.example .env
# Edit .env: set a real JWT_SECRET_KEY and your ANTHROPIC_API_KEY / VOYAGE_API_KEY
python -c "import secrets; print(secrets.token_urlsafe(64))"

cd ..
docker compose up -d --build
docker compose exec api alembic upgrade head
```

Visit **http://localhost:8000/docs**. Both Postgres+pgvector and the app
(with Tesseract's aze/rus/eng language packs baked into its image via
`backend/Dockerfile`) are running in containers.

**Note**: the API image is built from source, not bind-mounted - after
pulling backend code changes, re-run `docker compose build api && docker
compose up -d api` (and `alembic upgrade head` if there's a new migration)
for the running container to pick them up. A code change alone does not
affect an already-running container.

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
# Edit .env — set a real JWT_SECRET_KEY and your ANTHROPIC_API_KEY / VOYAGE_API_KEY
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

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Visit **http://localhost:5173**. The dev server proxies `/api` to the backend
on `:8000` (see `vite.config.js`), so make sure the backend is running first.
For production, `npm run build` outputs static files in `frontend/dist/`.

**What it does**: sign in/register → vacancies list → **+ New vacancy** (paste
a raw JD, everything else — required/preferred skills, education
requirement, experience minimum, wage budget — is auto-extracted
server-side) → open a vacancy, drag/drop résumés, **Process N résumés**
(uploads, parses, and scores the whole vacancy in one action) → ranked
candidate list, each row expandable into the full score breakdown
(component contributions, matched/missing skills with the resume text that
satisfied each one, per-job relevance with the AI's reasoning, education
pass/fail, wage fit — including inline wage entry if a candidate's resume
didn't state one). Each row also has its own **Recompute** button, for
re-scoring just that candidate (e.g. after entering their wage, or after a
scoring-logic change) without re-running the whole vacancy.

## Glorri.az integration (candidate import)

Pulls a vacancy's fresh applicants directly from Glorri instead of a
recruiter downloading résumés from Glorri and re-uploading them here by
hand. One-directional (Glorri → this tool) and manually triggered - no
webhook or scheduled polling.

- Set `GLORRI_API_TOKEN` in `.env` (static bearer token from Glorri).
- On a vacancy, set **Glorri.az job ID** (via Edit vacancy) to that job's
  `id` from Glorri, then use **Sync from Glorri** on the vacancy page (or
  `POST /vacancies/{id}/sync-glorri`).
- Only Glorri's `APPLIED` stage is imported - candidates already moved to
  `SHORTLISTED`/`LEFT`/etc. in Glorri have already been triaged there.
  Company-wide blacklisted candidates (by email) are always excluded.
- Each imported résumé goes through the exact same pipeline as a manual
  upload (content-hash reuse, AI structuring, embedding). Candidates with
  no `resumeUrl` in Glorri can't be parsed and are reported, not imported.
- Safe to re-run: candidates already imported in a previous sync (matched
  by Glorri's own candidate id, stored on `Candidate.glorri_candidate_id`)
  are skipped, never duplicated.

See `app/services/integrations/glorri_client.py` for the Glorri API details
(export-then-poll-then-download; Glorri has no per-job "new applicants"
endpoint, only a company-wide export) and `sync_glorri_applicants` in
`api/routes/candidates.py` for the import/filtering logic.

## Try it via curl

```bash
# Register + log in
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"recruiter@company.az","password":"password123","full_name":"Aysel Mammadova"}'
curl -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=recruiter@company.az&password=password123"
# -> {"access_token": "...", "token_type": "bearer"}

TOKEN="<paste access_token here>"

# Create a vacancy (manual JD entry - or paste a full JD and let extraction fill in the rest)
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

# Upload resumes (PDF/DOCX) against that vacancy
curl -X POST http://localhost:8000/api/v1/vacancies/$VACANCY_ID/candidates \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@/path/to/resume1.pdf" \
  -F "files=@/path/to/resume2.docx"

# Score + rank
curl -X POST http://localhost:8000/api/v1/vacancies/$VACANCY_ID/score \
  -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/api/v1/vacancies/$VACANCY_ID/ranking \
  -H "Authorization: Bearer $TOKEN"
```

A few things worth checking as you test:
- Try at least one **scanned/image-only PDF** to confirm the OCR fallback fires.
- Try one resume in each of **Azerbaijani, Russian, and English**.
- If `ANTHROPIC_API_KEY` is missing/invalid, uploads still succeed at the HTTP
  level but `parsing_status` will be `"failed"` with the error in
  `parsing_error` - check there before assuming something else is broken.
- Uploading the *same file* (by content hash) to a second vacancy skips
  re-parsing entirely and reuses the first parse's structured data/embedding.

## Project layout
```
backend/
  app/
    core/                     # config, JWT + password hashing
    db/                       # SQLAlchemy engine/session, declarative base
    models/                   # User, Vacancy, Candidate, Match,
                               #   CandidateJobRelevance, CandidateSkillRelevance
    schemas/                  # Pydantic request/response models
    api/routes/                # auth, users, vacancies, candidates, matching
    services/
      parsing/                # text_extraction, ai_structurer, canonicalizer,
                               #   jd_structurer, experience_calculator, pipeline
      embeddings/              # voyage_client, text_builders
      integrations/            # glorri_client - candidate import from Glorri.az
      scoring/                 # engine (combines everything below into one score)
        skill_matcher.py       #   literal token matching (the auditable first pass)
        skill_relevance_judge.py  # semantic fallback for what the token pass misses
        relevance_judge.py     #   AI-judged per-job relevance vs. vacancy responsibilities
        education_matcher.py   #   standard-ladder + enrollment-window modes
        wage_matcher.py         #  budget fit + hard veto
        relevance.py            #  relevance-weighted experience-years math
    main.py
  alembic/versions/           # migrations, one per schema change
  Dockerfile                  # app image w/ Tesseract + aze/rus/eng language packs
  tests/test_scoring_scenarios.py  # ad-hoc engine scenarios, no DB/API keys needed
frontend/
  src/                        # React app (App.jsx, ScoreBreakdown.jsx, api.js, ...)
docker-compose.yml            # Postgres+pgvector + the app
```

## How scoring works

Every candidate gets one 0-100 score per vacancy: a flat weighted sum of six
components, always paired with a breakdown that says exactly how it was
reached — nothing shown to a recruiter is a bare number they can't
interrogate. Weights are per-vacancy configurable
(`Vacancy.scoring_weights`, must sum to 1.0); defaults
(`DEFAULT_SCORING_WEIGHTS` in `app/models/vacancy.py`):

| Component | Default weight | What it measures |
|---|---|---|
| responsibilities | 0.45 | best-matching job vs. what the role actually involves (AI-judged) |
| wage | 0.15 | salary expectation vs. budget, with a hard veto above 2x |
| experience | 0.15 | relevance-weighted years vs. the stated minimum |
| required skills | 0.10 | fraction matched, literal + semantic fallback |
| education | 0.10 | pass/fail (two modes - see below), with a written reason |
| preferred skills | 0.05 | same matching as required, deliberately lighter - a bonus, not a gate |

### Responsibilities & experience: AI-judged relevance, not embeddings

Each of a candidate's jobs is rated 0.0-1.0 for how relevant it is to the
vacancy's responsibilities, by a single batched Claude call per candidate
(`relevance_judge.py`), cached per (candidate, vacancy) so re-scoring after a
weight change is free. The **responsibilities** score is the single
best-matching job's relevance (not an average - a strong relevant job
shouldn't be dragged down by an unrelated one). **Experience** years are
weighted by that same per-job relevance before comparing to the minimum, so
ten years in an unrelated field no longer sails past a three-year
requirement, but nothing is silently erased either. Overlapping job periods
are split into disjoint segments and credited at the highest relevance
covering each one, so concurrent jobs are never double-counted.

This was originally cosine similarity between job text and vacancy
responsibilities, and was replaced after real-world testing showed it
doesn't work: similarity for multilingual professional prose sits in a
narrow band regardless of actual fit, and tracks text *length* more than
meaning (three jobs with the identical title scored three different
numbers). Judging "is this job relevant to that role" is a
language-understanding task, which is what an LLM is actually good at - see
`relevance_judge.py`'s own docstring for the full writeup. The embedding
comparison still exists as a last-resort fallback if the judgement call
itself fails.

### Skills: literal matching first, semantic fallback second

`skill_matcher.py` is the always-on, free first pass: a requirement matches
if enough of its meaningful words (both its original wording and its
English canonical translation - see "Cross-language matching" below) appear
verbatim somewhere in the resume (skills, certificates, work history,
education). It's deliberately literal rather than semantic, so every match
is explainable as "these words matched that text."

That also means real false negatives: plural/singular mismatches
("visualizations" vs. "visualization"), or a skill demonstrated only through
described work rather than matching keywords ("collected, cleaned, and
validated large datasets" *is* data handling, but never says "handling").
For any requirement the literal pass can't confirm, `skill_relevance_judge.py`
asks an LLM whether the resume's actual content demonstrates it - cached per
(candidate, vacancy, requirement) so, again, re-scoring is free. It's
deliberately conservative (a vague/tangential connection is not a match) with
one carve-out: genuine MS Excel use is treated as sufficient evidence for
baseline features like pivot tables/basic formulas, since CVs describe what
the work was for rather than which menu commands were used - every other
tool keeps the strict standard.

This mirrors the same "LLM judgement over embeddings" reasoning as
relevance judging above, rather than a vector-similarity fallback - see
`skill_relevance_judge.py`'s docstring.

### Cross-language matching

A requirement written in Azerbaijani ("vizuallaşdırma") should match an
equivalent English candidate skill ("Data visualization") even though they
share no literal tokens. `canonicalizer.py` computes an English canonical
form for both a vacancy's requirements and a candidate's skills (one small
Claude call each, best-effort - falls back to the original phrases on
failure). The matcher then scores a requirement's *original* wording and its
*canonical* form **separately** and takes the better of the two, not their
token union (unioning was tried first and found to inflate the token count
whenever two phrasings share no literal words - the normal cross-language
case - making the match threshold *harder* to clear, not easier).

### Education: two modes

- **Standard mode** (`accepts_current_students = False`): the usual ladder -
  a higher completed degree satisfies a lower requirement.
- **Enrollment-window mode** (`accepts_current_students = True`, typical of
  internship/graduate-programme postings, with `graduation_year_min/max`):
  an unrelated higher degree earned years ago does **not** auto-qualify.
  Passes if either (a) currently enrolled at exactly the required level with
  an expected graduation inside the window, or (b) holds *any* **completed**
  degree at or above the required level whose graduation year falls inside
  the window - covering both someone who finished the required level
  in-window and has since started a higher one, and someone whose lower
  degree is out-of-window but who went on to complete a higher one that
  itself lands in the window.

Degree names are read via an AZ/RU/EN alias table (`degree_text_to_level`),
not a raw substring match - an entry reading "Bakalavr" or "бакалавр" never
contains the literal substring "bachelor".

### Wage

Full credit at or under budget, gradual falloff above it, and a **hard veto**
(whole score snaps to 0) at 2x the budget ceiling or more - not a realistic
hire regardless of everything else
(`wage_matcher.py::HARD_FAIL_MULTIPLE`). No stated wage expectation scores
as neutral (full credit) rather than penalizing a candidate for an
unknown - worth keeping in mind when comparing a candidate who disclosed a
high expectation against one who simply didn't say.

### Non-CV uploads

Resume structuring classifies `document_type`
(`resume`/`not_a_resume`/`blank_or_unreadable`); anything that isn't a
résumé short-circuits to a score of 0 with an explicit reason rather than
being evaluated against requirements it was never meant to satisfy.

---

`backend/tests/test_scoring_scenarios.py` runs the engine through a set of
scenarios (strong match, over-qualified degree, final-year student, non-CV
upload, over-budget wage, missing embeddings, and more) with no DB or API
keys required - a quick sanity check after touching scoring logic:
```bash
python backend/tests/test_scoring_scenarios.py
```
