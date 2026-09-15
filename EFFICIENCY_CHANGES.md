# Efficiency Pass — Cost & Latency Reductions

This pass modifies the existing `phase6-v4` codebase in place. **No scoring
logic was touched** — the pre-existing scoring regression test
(`backend/tests/test_scoring_scenarios.py`) produces byte-for-byte identical
output before and after. Every change reduces cost or wall-clock time without
altering any scored result.

## What changed

### 1. Removed dead per-job embedding calls
**File:** `backend/app/services/parsing/pipeline.py` (`_sync_job_embeddings`)

Every resume parse was calling the Voyage embedding API once per job in the
candidate's work history and storing the result in `candidate_job_embeddings`.
That table is read by nothing — per-job relevance moved to AI judgement
(`scoring/relevance_judge.py`) and the old embedding path was never removed.
These calls were pure wasted spend on every upload.

`_sync_job_embeddings` is now a no-op that only clears any legacy rows. The
table/model is left in place (dropping it is a follow-up migration); nothing
reads it, so scoring is unaffected.

**Saving:** for a candidate with N jobs, N embedding calls → 0.

### 2. Fixed prompt caching (was silently defeated)
**Files:** `backend/app/services/parsing/ai_structurer.py`,
`backend/app/services/scoring/relevance_judge.py`

The structuring system prompt had `today's date` interpolated into it on every
call, making it a unique string each time and disabling Claude's prompt cache
entirely. The date now goes in the *user* message; the (large, static) system
prompt is marked `cache_control: ephemeral` so it's billed at the reduced
cached rate after the first call. Same caching fix applied to the relevance
judge's static system prompt.

**Saving:** most of the system-prompt input tokens on every structuring and
relevance call after the first.

### 3. Model tiering + escalation for resume structuring
**Files:** `backend/app/services/parsing/ai_structurer.py`,
`backend/app/core/config.py`

Structuring ran on Opus (`ANTHROPIC_MODEL`) for every resume. It now runs on a
cheaper default (`ANTHROPIC_MODEL_STRUCTURING_DEFAULT`, Haiku-class) and
**automatically escalates to Opus** when the cheap result looks unreliable:
tool not called, substantive input but empty output, or missing
`document_type`. See `_needs_escalation`.

**This escalation tier is the success-rate safety net for this change and must
not be removed to cut cost further.** Job-relevance judgement is deliberately
left on Opus — the code's own analysis (see `relevance_judge.py` docstring)
shows that task needs the stronger model.

**Saving:** the model-price delta on the majority of resumes that the cheap
tier handles correctly; the minority that escalate cost one extra cheap call.

### 4. Removed a forced paid call on single-candidate re-score
**File:** `backend/app/api/routes/matching.py` (`score_single_candidate`)

Re-scoring one candidate always passed `force_refresh=True`, triggering a fresh
paid relevance-judgement call even when nothing relevant changed (e.g. entering
a wage, tuning weights). It's now cache-first by default — the relevance cache
is already correctly invalidated when a reparse changes the job count. A new
`?refresh_relevance=true` query param preserves the explicit-refresh case
(e.g. after editing a vacancy's responsibilities).

**Saving:** one relevance call per single-candidate re-score in the common case.

### 5. Cross-vacancy parse reuse by content hash
**Files:** `backend/app/models/candidate.py` (new `file_hash` column),
`backend/alembic/versions/0011_candidate_file_hash.py` (migration),
`backend/app/api/routes/candidates.py`,
`backend/app/services/parsing/pipeline.py` (`apply_cached_parse`)

Candidates had no dedup concept: the same resume uploaded to two vacancies was
parsed twice (extract + structure + embed, all paid). Each upload's bytes are
now hashed (SHA-256). If an identical file was already parsed successfully
anywhere, its results are copied onto the new row and the AI pipeline is
skipped — reported as `status: "cached"`.

**Saving:** a full parse (extraction + 1–2 Claude calls + 1 embedding call) per
duplicate upload. Candidate pools overlap heavily across vacancies in real
recruiting, so this compounds over time.

### 6. Concurrent bulk-upload processing
**Files:** `backend/app/services/parsing/pipeline.py` (`parse_and_store_many`,
`PipelineResult`, `_run_extract_and_structure`),
`backend/app/api/routes/candidates.py`, `backend/app/core/config.py`

Bulk uploads processed files one at a time, synchronously, inside the request —
each file's blocking Claude/Voyage calls fully serialized (the codebase itself
flagged this as a known bottleneck). The blocking pipeline is now a pure,
DB-free function run concurrently across files via `anyio.to_thread`, bounded by
`MAX_CONCURRENT_PARSES` (default 8). DB writes stay sequential (single async
session). This is a **latency** win, not a cost one — the same calls happen,
but overlapped.

**Saving:** wall-clock time for a batch of N resumes drops from ~N call
latencies to ~N / MAX_CONCURRENT_PARSES.

## New / changed settings
- `ANTHROPIC_MODEL_STRUCTURING_DEFAULT` (default `claude-haiku-4-5-20251001`)
- `MAX_CONCURRENT_PARSES` (default `8`)
- `?refresh_relevance=true` on `POST /vacancies/{id}/candidates/{id}/score`

## Migration
Run `alembic upgrade head` to apply `0011_candidate_file_hash` before using the
updated upload route (it references `candidates.file_hash`).

## Tests
- `backend/tests/test_scoring_scenarios.py` — unchanged output (scoring untouched).
- `backend/tests/test_efficiency_pass.py` — new: verifies `apply_cached_parse`
  copies every parsed field, and the escalation triggers fire correctly.

---

# UI additions (post-efficiency-pass)

## Recompute scores button
**File:** `frontend/src/App.jsx`

Changing a vacancy's scoring weights never rescored on its own — the backend
`POST /vacancies/{id}/score` endpoint existed and the API client already
wrapped it (`scoreVacancy`), but nothing in the UI called it outside the
upload flow, so a weight change left the ranking stale with no way to refresh.

Added a **Recompute scores** button to the vacancy toolbar (next to Edit /
Delete). It re-runs scoring for every candidate against the vacancy's current
weights and requirements, then reloads the ranking. This is cheap by design:
it reuses stored parses, embeddings, and cached relevance judgements — nothing
is re-parsed or re-embedded. A hint under the weights editor now tells
recruiters to use this button after saving new weights.

## Candidate contact details in the ranking view
**Files:** `backend/app/models/candidate.py`,
`backend/app/schemas/candidate.py`, `frontend/src/App.jsx`,
`frontend/src/styles.css`

The expanded candidate view now shows the parsed **email** and **phone**. These
were already extracted into `structured_data.contact` but weren't surfaced in
the lightweight ranking payload (`CandidateSummary`). Added `email` and `phone`
convenience properties on the `Candidate` model — mirroring the existing
`full_name` pattern, so the list/ranking endpoints get the two fields recruiters
need without shipping the whole `structured_data` blob — and exposed them on
`CandidateSummary`. When a resume didn't list a value, the field is `null` and
the UI renders an em-dash (—) placeholder, as requested. No new DB column or
migration is needed (these are derived from already-stored data).
