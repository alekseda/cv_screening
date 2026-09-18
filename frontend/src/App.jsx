import { useEffect, useState, useCallback } from "react";
import * as api from "./api.js";
import ScoreBreakdown from "./ScoreBreakdown.jsx";
import WageEditor, { CURRENCIES } from "./WageEditor.jsx";

export default function App() {
  const [user, setUser] = useState(null);
  const [booting, setBooting] = useState(true);
  const [activeVacancy, setActiveVacancy] = useState(null);

  useEffect(() => {
    if (!api.getToken()) {
      setBooting(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => api.setToken(null))
      .finally(() => setBooting(false));
  }, []);

  function handleLogout() {
    api.setToken(null);
    setUser(null);
    setActiveVacancy(null);
  }

  if (booting) return null;
  if (!user) return <AuthScreen onAuthed={setUser} />;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">Merit</span>
          <span className="brand-sub">Candidate Screening</span>
        </div>
        <div className="topbar-user">
          <span>{user.full_name}</span>
          <button className="linkbtn" onClick={handleLogout}>Sign out</button>
        </div>
      </header>

      <main className="container">
        {activeVacancy ? (
          <VacancyDetail
            vacancyId={activeVacancy}
            onBack={() => setActiveVacancy(null)}
          />
        ) : (
          <VacancyList onOpen={setActiveVacancy} />
        )}
      </main>
    </div>
  );
}

/* ---------------- Auth ---------------- */
function AuthScreen({ onAuthed }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "register") {
        await api.register(email, password, fullName);
      }
      await api.login(email, password);
      const u = await api.me();
      onAuthed(u);
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="card card-pad auth-card">
        <div className="auth-head">
          <div className="brand-mark">Merit</div>
          <div className="brand-sub" style={{ marginTop: 6 }}>Candidate Screening</div>
        </div>

        <div className="auth-tabs">
          <button className={`auth-tab ${mode === "login" ? "active" : ""}`} onClick={() => setMode("login")}>
            Sign in
          </button>
          <button className={`auth-tab ${mode === "register" ? "active" : ""}`} onClick={() => setMode("register")}>
            Create account
          </button>
        </div>

        {error && <div className="banner banner-error">{error}</div>}

        <form onSubmit={submit}>
          {mode === "register" && (
            <div className="field">
              <label>Full name</label>
              <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
            </div>
          )}
          <div className="field">
            <label>Work email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field">
            <label>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
          </div>
          <button className="btn btn-primary btn-lg" style={{ width: "100%" }} disabled={busy}>
            {busy ? <span className="spinner" /> : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}

/* ---------------- Vacancy list ---------------- */
function VacancyList({ onOpen }) {
  const [vacancies, setVacancies] = useState(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [removingId, setRemovingId] = useState(null);

  const load = useCallback(() => {
    api.listVacancies().then(setVacancies).catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  async function handleRemove(e, id, title) {
    e.stopPropagation(); // don't also open the vacancy
    if (!window.confirm(`Delete "${title}"? All of its candidates and scores will be removed too. This cannot be undone.`)) return;
    setRemovingId(id);
    try {
      await api.deleteVacancy(id);
      load();
    } catch (err) {
      alert(err.message || "Could not delete vacancy");
    } finally {
      setRemovingId(null);
    }
  }

  if (creating) {
    return (
      <CreateVacancy
        onCancel={() => setCreating(false)}
        onCreated={(v) => {
          setCreating(false);
          onOpen(v.id);
        }}
      />
    );
  }

  return (
    <>
      <div className="eyebrow">Open positions</div>
      <div className="toolbar">
        <h1 className="page-title">Vacancies</h1>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>+ New vacancy</button>
      </div>

      {error && <div className="banner banner-error">{error}</div>}

      {vacancies === null ? (
        <div className="progress-note"><span className="spinner dark" /> Loading…</div>
      ) : vacancies.length === 0 ? (
        <div className="empty">
          <div className="dropzone-icon">◇</div>
          <h3>No vacancies yet</h3>
          <p>Create your first vacancy to start screening candidates.</p>
        </div>
      ) : (
        <div className="vac-grid">
          {vacancies.map((v) => (
            <div className="card vac-card" key={v.id} onClick={() => onOpen(v.id)}>
              <button
                className="vac-remove"
                onClick={(e) => handleRemove(e, v.id, v.title)}
                disabled={removingId === v.id}
                title="Delete vacancy"
              >
                {removingId === v.id ? <span className="spinner dark" /> : "✕"}
              </button>
              <h3>{v.title}</h3>
              <div className="vac-meta">
                {v.required_skills?.length > 0 && <span>◆ {v.required_skills.length} required skills</span>}
                {v.min_experience_years > 0 && <span>◷ {v.min_experience_years}y min</span>}
                {v.education_requirement !== "none" && <span>◈ {v.education_requirement}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/* ---------------- Create vacancy ---------------- */
function CreateVacancy({ onCancel, onCreated }) {
  const [description, setDescription] = useState("");
  const [title, setTitle] = useState("");
  const [wageMin, setWageMin] = useState("");
  const [wageMax, setWageMax] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const payload = { description };
      if (title.trim()) payload.title = title.trim();
      if (wageMin) payload.wage_min = Number(wageMin);
      if (wageMax) payload.wage_max = Number(wageMax);
      const v = await api.createVacancy(payload);
      onCreated(v);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className="back-link" onClick={onCancel}>← Back to vacancies</button>
      <div className="eyebrow">New position</div>
      <h1 className="page-title">Create a vacancy</h1>
      <p className="page-lead">
        Paste the full job description. The title, responsibilities, required and preferred
        skills, experience and education requirements are read from it automatically — you can
        refine them afterward.
      </p>

      {error && <div className="banner banner-error">{error}</div>}

      <div className="card card-pad" style={{ maxWidth: 720 }}>
        <form onSubmit={submit}>
          <div className="field">
            <label>Job description</label>
            <div className="hint">The raw posting, in Azerbaijani, Russian, or English.</div>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={12}
              placeholder="Paste the full vacancy text here…"
              required
            />
          </div>
          <div className="field">
            <label>Title <span style={{ color: "var(--slate)", fontWeight: 400 }}>· optional override</span></label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Left blank — read from the description" />
          </div>
          <div className="field-row">
            <div className="field">
              <label>Budget minimum <span style={{ color: "var(--slate)", fontWeight: 400 }}>· optional</span></label>
              <input type="number" value={wageMin} onChange={(e) => setWageMin(e.target.value)} placeholder="e.g. 1000" min="0" />
            </div>
            <div className="field">
              <label>Budget maximum <span style={{ color: "var(--slate)", fontWeight: 400 }}>· optional</span></label>
              <input type="number" value={wageMax} onChange={(e) => setWageMax(e.target.value)} placeholder="e.g. 1500" min="0" />
            </div>
          </div>
          <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
            <button className="btn btn-primary btn-lg" disabled={busy}>
              {busy ? <><span className="spinner" /> Reading description…</> : "Create vacancy"}
            </button>
            <button type="button" className="btn btn-ghost btn-lg" onClick={onCancel} disabled={busy}>Cancel</button>
          </div>
        </form>
      </div>
    </>
  );
}

const EDUCATION_LEVELS = ["none", "high_school", "bachelor", "master", "phd"];
const WEIGHT_FIELDS = [
  ["responsibilities_weight", "Role match"],
  ["wage_weight", "Wage fit"],
  ["experience_weight", "Experience"],
  ["required_skills_weight", "Required skills"],
  ["education_weight", "Education"],
  ["preferred_skills_weight", "Preferred skills"],
];

function EditVacancy({ vacancy, onCancel, onSaved }) {
  const [title, setTitle] = useState(vacancy.title || "");
  const [description, setDescription] = useState(vacancy.description || "");
  const [responsibilities, setResponsibilities] = useState(vacancy.responsibilities || "");
  const [requiredSkills, setRequiredSkills] = useState((vacancy.required_skills || []).join("\n"));
  const [preferredSkills, setPreferredSkills] = useState((vacancy.preferred_skills || []).join("\n"));
  const [minExperience, setMinExperience] = useState(vacancy.min_experience_years ?? 0);
  const [educationRequirement, setEducationRequirement] = useState(vacancy.education_requirement || "none");
  const [acceptsCurrentStudents, setAcceptsCurrentStudents] = useState(!!vacancy.accepts_current_students);
  const [gradMin, setGradMin] = useState(vacancy.graduation_year_min ?? "");
  const [gradMax, setGradMax] = useState(vacancy.graduation_year_max ?? "");
  const [wageMin, setWageMin] = useState(vacancy.wage_min ?? "");
  const [wageMax, setWageMax] = useState(vacancy.wage_max ?? "");
  const [wageCurrency, setWageCurrency] = useState(vacancy.wage_currency || "AZN");
  const [weights, setWeights] = useState({ ...vacancy.scoring_weights });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const weightSum = Object.values(weights).reduce((a, b) => a + (Number(b) || 0), 0);
  const weightValid = Math.abs(weightSum - 1) < 0.01;

  function setWeight(key, value) {
    setWeights((w) => ({ ...w, [key]: value }));
  }

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (!weightValid) {
      setError(`Scoring weights must sum to 1.00 — they currently sum to ${weightSum.toFixed(2)}`);
      return;
    }
    setBusy(true);
    try {
      const payload = {
        title: title.trim(),
        description,
        responsibilities: responsibilities.trim() || null,
        required_skills: requiredSkills.split("\n").map((s) => s.trim()).filter(Boolean),
        preferred_skills: preferredSkills.split("\n").map((s) => s.trim()).filter(Boolean),
        min_experience_years: Number(minExperience) || 0,
        education_requirement: educationRequirement,
        accepts_current_students: acceptsCurrentStudents,
        graduation_year_min: gradMin === "" ? null : Number(gradMin),
        graduation_year_max: gradMax === "" ? null : Number(gradMax),
        wage_min: wageMin === "" ? null : Number(wageMin),
        wage_max: wageMax === "" ? null : Number(wageMax),
        wage_currency: wageCurrency,
        scoring_weights: Object.fromEntries(
          WEIGHT_FIELDS.map(([key]) => [key, Number(weights[key]) || 0])
        ),
      };
      const updated = await api.updateVacancy(vacancy.id, payload);
      onSaved(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className="back-link" onClick={onCancel}>← Back</button>
      <div className="eyebrow">Editing</div>
      <h1 className="page-title">{vacancy.title}</h1>
      <p className="page-lead">
        Changes here take effect immediately — re-run scoring afterward from the vacancy page
        to reflect them in the ranking.
      </p>

      {error && <div className="banner banner-error">{error}</div>}

      <div className="card card-pad" style={{ maxWidth: 720 }}>
        <form onSubmit={submit}>
          <div className="field">
            <label>Title</label>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} required />
          </div>

          <div className="field">
            <label>Job description</label>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={8} required />
          </div>

          <div className="field">
            <label>Responsibilities</label>
            <div className="hint">What the role actually involves day-to-day — drives per-job relevance weighting.</div>
            <textarea value={responsibilities} onChange={(e) => setResponsibilities(e.target.value)} rows={6} />
          </div>

          <div className="field">
            <label>Required skills</label>
            <div className="hint">One per line.</div>
            <textarea value={requiredSkills} onChange={(e) => setRequiredSkills(e.target.value)} rows={4} />
          </div>

          <div className="field">
            <label>Preferred skills</label>
            <div className="hint">One per line.</div>
            <textarea value={preferredSkills} onChange={(e) => setPreferredSkills(e.target.value)} rows={3} />
          </div>

          <div className="field-row">
            <div className="field">
              <label>Minimum experience (years)</label>
              <input type="number" min="0" step="0.5" value={minExperience} onChange={(e) => setMinExperience(e.target.value)} />
            </div>
            <div className="field">
              <label>Education requirement</label>
              <select value={educationRequirement} onChange={(e) => setEducationRequirement(e.target.value)}>
                {EDUCATION_LEVELS.map((lvl) => (
                  <option key={lvl} value={lvl}>{lvl.replace("_", " ")}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="field">
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600 }}>
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={acceptsCurrentStudents}
                onChange={(e) => setAcceptsCurrentStudents(e.target.checked)}
              />
              Accepts final-year students / recent graduates
            </label>
          </div>

          {acceptsCurrentStudents && (
            <div className="field-row">
              <div className="field">
                <label>Graduation year — earliest accepted</label>
                <input type="number" value={gradMin} onChange={(e) => setGradMin(e.target.value)} placeholder="No limit" />
              </div>
              <div className="field">
                <label>Graduation year — latest accepted</label>
                <input type="number" value={gradMax} onChange={(e) => setGradMax(e.target.value)} placeholder="No limit" />
              </div>
            </div>
          )}

          <div className="field-row">
            <div className="field">
              <label>Budget minimum</label>
              <input type="number" min="0" value={wageMin} onChange={(e) => setWageMin(e.target.value)} placeholder="No minimum" />
            </div>
            <div className="field">
              <label>Budget maximum</label>
              <input type="number" min="0" value={wageMax} onChange={(e) => setWageMax(e.target.value)} placeholder="No maximum" />
            </div>
            <div className="field" style={{ maxWidth: 110 }}>
              <label>Currency</label>
              <select value={wageCurrency} onChange={(e) => setWageCurrency(e.target.value)}>
                {["AZN", "USD", "EUR", "RUB", "TRY"].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>

          <div className="field">
            <label>Scoring weights</label>
            <div className="hint">
              How much each component contributes to the total score. Must sum to 1.00 —
              currently <b style={{ color: weightValid ? "var(--good)" : "var(--bad)" }}>{weightSum.toFixed(2)}</b>.
            </div>
            <div className="weight-grid">
              {WEIGHT_FIELDS.map(([key, label]) => (
                <div className="weight-input" key={key}>
                  <span>{label}</span>
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={weights[key] ?? 0}
                    onChange={(e) => setWeight(key, e.target.value)}
                  />
                </div>
              ))}
            </div>
            <div className="hint" style={{ marginTop: 8 }}>
              Changing weights doesn't rescore on its own. After saving, use
              <b> Recompute scores</b> on the vacancy to apply them to the ranking.
            </div>
          </div>

          <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
            <button className="btn btn-primary btn-lg" disabled={busy || !weightValid}>
              {busy ? <><span className="spinner" /> Saving…</> : "Save changes"}
            </button>
            <button type="button" className="btn btn-ghost btn-lg" onClick={onCancel} disabled={busy}>Cancel</button>
          </div>
        </form>
      </div>
    </>
  );
}

/* ---------------- Vacancy detail: upload + rank ---------------- */
function VacancyDetail({ vacancyId, onBack }) {
  const [vacancy, setVacancy] = useState(null);
  const [ranking, setRanking] = useState(null);
  const [candidates, setCandidates] = useState(null);
  const [retrying, setRetrying] = useState(false);
  const [retryNote, setRetryNote] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [rescoring, setRescoring] = useState(false);

  const loadVacancy = useCallback(() => {
    api.getVacancy(vacancyId).then(setVacancy).catch((e) => setError(e.message));
  }, [vacancyId]);

  const loadRanking = useCallback(() => {
    api
      .getRanking(vacancyId)
      .then(setRanking)
      .catch((e) => {
        if (e.status === 404) setRanking([]); // no scores yet — not an error
        else setError(e.message);
      });
  }, [vacancyId]);

  // Separate from the ranking: /ranking only returns candidates that have a
  // Match row, and it's the parse failures that need surfacing here. This is
  // the persistent source for the "Needs attention" panel — unlike the
  // per-upload results list, it survives a refresh.
  const loadCandidates = useCallback(() => {
    api.listCandidates(vacancyId).then(setCandidates).catch((e) => setError(e.message));
  }, [vacancyId]);

  const refreshAll = useCallback(() => {
    loadRanking();
    loadCandidates();
  }, [loadRanking, loadCandidates]);

  useEffect(() => {
    loadVacancy();
    loadRanking();
    loadCandidates();
  }, [vacancyId, loadVacancy, loadRanking, loadCandidates]);

  async function handleRescore() {
    setError("");
    setRescoring(true);
    try {
      // Re-runs scoring for every candidate against the vacancy's CURRENT
      // weights/requirements. Cheap: reuses stored parses, embeddings and
      // cached relevance judgements — nothing is re-parsed or re-embedded.
      await api.scoreVacancy(vacancyId);
      loadRanking();
    } catch (err) {
      setError(err.message || "Could not recompute scores");
    } finally {
      setRescoring(false);
    }
  }

  async function handleRetryFailed() {
    setError("");
    setRetryNote("");
    setRetrying(true);
    try {
      const res = await api.reparseFailed(vacancyId);
      // Re-score straight away: a recovered candidate has no Match row yet (or
      // has a stale 0-score one), so without this the ranking would still be
      // missing them and the recruiter would have to click a second button.
      if (res.recovered > 0) await api.scoreVacancy(vacancyId);
      const parts = [`${res.recovered} recovered`];
      if (res.still_failed) parts.push(`${res.still_failed} still failing`);
      if (res.needs_reupload) parts.push(`${res.needs_reupload} need re-uploading`);
      setRetryNote(`Retried ${res.attempted}: ${parts.join(", ")}.`);
      refreshAll();
    } catch (err) {
      setError(err.message || "Could not retry failed résumés");
    } finally {
      setRetrying(false);
    }
  }

  async function handleDeleteVacancy() {
    if (!vacancy) return;
    if (!window.confirm(`Delete "${vacancy.title}"? All of its candidates and scores will be removed too. This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await api.deleteVacancy(vacancyId);
      onBack();
    } catch (err) {
      setError(err.message || "Could not delete vacancy");
      setDeleting(false);
    }
  }

  if (editing && vacancy) {
    return (
      <EditVacancy
        vacancy={vacancy}
        onCancel={() => setEditing(false)}
        onSaved={(updated) => {
          setVacancy(updated);
          setEditing(false);
        }}
      />
    );
  }

  return (
    <>
      <button className="back-link" onClick={onBack}>← All vacancies</button>
      {error && <div className="banner banner-error">{error}</div>}

      <div className="toolbar">
        <div>
          <div className="eyebrow">Screening</div>
          <h1 className="page-title">{vacancy ? vacancy.title : "…"}</h1>
        </div>
        {vacancy && (
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn btn-primary" onClick={handleRescore} disabled={rescoring}>
              {rescoring ? <span className="spinner" /> : "Recompute scores"}
            </button>
            <button className="btn btn-ghost" onClick={() => setEditing(true)}>Edit vacancy</button>
            <button className="btn btn-ghost btn-danger" onClick={handleDeleteVacancy} disabled={deleting}>
              {deleting ? <span className="spinner dark" /> : "Delete vacancy"}
            </button>
          </div>
        )}
      </div>

      <UploadPanel vacancyId={vacancyId} onScored={refreshAll} defaultCurrency={vacancy?.wage_currency || "AZN"} />

      <NeedsAttentionPanel
        candidates={candidates}
        retrying={retrying}
        note={retryNote}
        onRetry={handleRetryFailed}
        onChanged={refreshAll}
      />

      <RankingPanel ranking={ranking} vacancyId={vacancyId} onChanged={refreshAll} />
    </>
  );
}

/* ---------------- Upload panel ---------------- */
function UploadPanel({ vacancyId, onScored, defaultCurrency = "AZN" }) {
  const [files, setFiles] = useState([]); // [{ id, file }]
  const [wages, setWages] = useState({}); // { [fileId]: { wage: string, currency: string } }
  const [results, setResults] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | uploading | scoring | done
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");

  function addFiles(list) {
    const pdfs = Array.from(list).filter(
      (f) => f.name.toLowerCase().endsWith(".pdf") || f.name.toLowerCase().endsWith(".docx")
    );
    const wrapped = pdfs.map((file) => ({
      id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file,
    }));
    setFiles((prev) => [...prev, ...wrapped]);
  }

  function removeFile(id) {
    setFiles((prev) => prev.filter((f) => f.id !== id));
    setWages((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  function setWageFor(id, patch) {
    setWages((prev) => ({
      ...prev,
      [id]: { wage: "", currency: defaultCurrency, ...prev[id], ...patch },
    }));
  }

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  }

  async function run() {
    if (files.length === 0) return;
    setError("");
    setResults(null);
    setPhase("uploading");
    try {
      // Same order as `files`, so the backend can zip each wage to its file.
      const wagesPayload = files.map(({ id }) => ({
        wage: wages[id]?.wage ? Number(wages[id].wage) : null,
        currency: wages[id]?.currency || defaultCurrency,
      }));
      const uploadResults = await api.uploadCandidates(vacancyId, files.map((f) => f.file), wagesPayload);
      // Carry the submitted wage along with each result (by matching order),
      // so the post-upload editor for successfully parsed candidates can be
      // prefilled rather than shown blank when a wage was already entered.
      const enriched = uploadResults.map((r, i) => ({
        ...r,
        submittedWage: wagesPayload[i]?.wage ?? null,
        submittedCurrency: wagesPayload[i]?.currency ?? defaultCurrency,
      }));
      setResults(enriched);
      setPhase("scoring");
      await api.scoreVacancy(vacancyId);
      setPhase("done");
      setFiles([]);
      setWages({});
      onScored();
    } catch (err) {
      setError(err.message);
      setPhase("idle");
    }
  }

  const working = phase === "uploading" || phase === "scoring";

  return (
    <div className="card card-pad" style={{ marginBottom: 32 }}>
      {error && <div className="banner banner-error">{error}</div>}

      <div
        className={`dropzone ${dragging ? "drag" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div className="dropzone-icon">↑</div>
        <h3>Upload résumés</h3>
        <p>Drag PDF or DOCX files here, or</p>
        <label className="btn btn-ghost btn-sm" style={{ marginTop: 12, display: "inline-flex" }}>
          Choose files
          <input
            type="file"
            accept=".pdf,.docx"
            multiple
            style={{ display: "none" }}
            onChange={(e) => addFiles(e.target.files)}
          />
        </label>
      </div>

      {files.length > 0 && (
        <div className="filelist">
          <div className="filelist-hint">
            Know a candidate's wage expectation already? Enter it now — if it's already well
            over budget, that résumé won't be processed at all.
          </div>
          {files.map(({ id, file }) => (
            <div className="fileitem upload-pending" key={id}>
              <div className="fmeta-row">
                <span className="fmeta">◲ {file.name}</span>
                {!working && (
                  <button className="linkbtn" onClick={() => removeFile(id)}>Remove</button>
                )}
              </div>
              <div className="wage-edit compact prewage">
                <input
                  type="number"
                  min="0"
                  placeholder="Wage expectation (optional)"
                  value={wages[id]?.wage ?? ""}
                  onChange={(e) => setWageFor(id, { wage: e.target.value })}
                  disabled={working}
                />
                <select
                  value={wages[id]?.currency ?? defaultCurrency}
                  onChange={(e) => setWageFor(id, { currency: e.target.value })}
                  disabled={working}
                >
                  {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>
          ))}
        </div>
      )}

      {results && (
        <div className="filelist">
          {results.map((r, i) => (
            <div className={`fileitem upload-result ${r.status === "success" ? "has-wage" : ""}`} key={i}>
              <div className="fmeta-row">
                <span className="fmeta">◲ {r.filename}</span>
                <span className={`fstatus ${r.status}`}>
                  {r.status === "success" && "Parsed"}
                  {r.status === "cached" && "Parsed earlier — reused"}
                  {r.status === "skipped" && "Skipped — wage over budget"}
                  {r.status === "rejected" && `Rejected — ${r.error}`}
                  {r.status === "failed" && `Failed — ${r.error}`}
                </span>
              </div>
              {r.status === "success" && r.candidate_id && (
                <WageEditor
                  vacancyId={vacancyId}
                  candidateId={r.candidate_id}
                  initialWage={r.submittedWage}
                  initialCurrency={r.submittedCurrency || defaultCurrency}
                  disabled={working}
                  compact
                  onSaved={onScored}
                />
              )}
              {r.status === "skipped" && (
                <div className="skip-note">{r.error}</div>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 18, display: "flex", alignItems: "center", gap: 16 }}>
        <button className="btn btn-accent btn-lg" onClick={run} disabled={files.length === 0 || working}>
          {phase === "uploading" && <><span className="spinner" /> Reading résumés…</>}
          {phase === "scoring" && <><span className="spinner" /> Scoring against the role…</>}
          {(phase === "idle" || phase === "done") && `Process ${files.length || ""} résumé${files.length === 1 ? "" : "s"}`.trim()}
        </button>
        {working && (
          <span className="progress-note">
            This can take a moment — each résumé is parsed, embedded, and scored.
          </span>
        )}
      </div>
    </div>
  );
}

/* ---------------- Needs attention (parse failures) ---------------- */
function NeedsAttentionPanel({ candidates, retrying, note, onRetry, onChanged }) {
  const [removingId, setRemovingId] = useState(null);

  // Anything that never produced a usable parse. These deliberately do NOT
  // appear in the ranking — a 0% row caused by a corrupt file is noise, not a
  // judgement about the candidate.
  //
  // Wage vetoes are excluded here on purpose: they aren't a processing
  // failure, they're a real screening outcome the recruiter configured. They
  // stay in the ranking at 0% with the veto as their stated reason.
  const problems = (candidates || []).filter(
    (c) => c.parsing_status !== "success" && c.parsing_status !== "skipped_wage_veto"
  );
  async function handleRemove(candidateId, name) {
    if (!window.confirm(`Remove ${name}? This cannot be undone.`)) return;
    setRemovingId(candidateId);
    try {
      await api.deleteCandidate(candidateId);
      onChanged();
    } catch (err) {
      alert(err.message || "Could not remove candidate");
    } finally {
      setRemovingId(null);
    }
  }

  if (candidates === null || problems.length === 0) return null;

  // Only "failed" is retryable — wage-vetoed rows were excluded on purpose,
  // and the backend's reparse-failed query ignores them too.
  const retryable = problems.filter((c) => c.parsing_status === "failed").length;

  return (
    <div className="card card-pad" style={{ marginBottom: 32 }}>
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <div>
          <div className="eyebrow">Needs attention</div>
          <h2 style={{ fontFamily: "var(--serif)", fontWeight: 600, fontSize: 20, color: "var(--ink)" }}>
            {problems.length} résumé{problems.length === 1 ? "" : "s"} not scored
          </h2>
        </div>
        {retryable > 0 && (
          <button className="btn btn-primary" onClick={onRetry} disabled={retrying}>
            {retrying ? <><span className="spinner" /> Retrying…</> : "Retry all failed"}
          </button>
        )}
      </div>

      <div className="filelist-hint" style={{ marginBottom: 12 }}>
        These aren't ranked because parsing didn't complete, so there's nothing to score
        against. Retrying re-runs the AI step on text already extracted — files aren't
        re-read and nothing is re-uploaded.
      </div>

      {note && <div className="banner banner-info">{note}</div>}

      <div className="filelist">
        {problems.map((c) => {
          const name = c.full_name || c.original_filename;
          return (
            <div className="fileitem upload-result" key={c.id}>
              <div className="fmeta-row">
                <span className="fmeta">◲ {name}</span>
                <span className={`fstatus ${c.parsing_status}`}>
                  {c.parsing_status === "failed" && "Parsing failed"}
                  {c.parsing_status === "pending" && "Still processing"}
                  {!["failed", "pending"].includes(c.parsing_status) && c.parsing_status}
                </span>
              </div>
              {c.parsing_error && <div className="skip-note">{c.parsing_error}</div>}
              <div style={{ marginTop: 8 }}>
                <button
                  className="linkbtn"
                  onClick={() => handleRemove(c.id, name)}
                  disabled={removingId === c.id}
                >
                  {removingId === c.id ? "Removing…" : "Remove"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------- Ranking panel ---------------- */
function RankingPanel({ ranking, vacancyId, onChanged }) {
  const [openId, setOpenId] = useState(null);
  const [removingId, setRemovingId] = useState(null);
  const [rescoringId, setRescoringId] = useState(null);

  async function handleRemove(e, candidateId, name) {
    e.stopPropagation(); // don't also toggle the row open/closed
    if (!window.confirm(`Remove ${name} from this vacancy? This cannot be undone.`)) return;
    setRemovingId(candidateId);
    try {
      await api.deleteCandidate(candidateId);
      onChanged();
    } catch (err) {
      alert(err.message || "Could not remove candidate");
    } finally {
      setRemovingId(null);
    }
  }

  async function handleRecompute(e, candidateId) {
    e.stopPropagation(); // don't also toggle the row open/closed
    setRescoringId(candidateId);
    try {
      await api.scoreSingleCandidate(vacancyId, candidateId);
      onChanged();
    } catch (err) {
      alert(err.message || "Could not recompute this candidate's score");
    } finally {
      setRescoringId(null);
    }
  }

  if (ranking === null) {
    return <div className="progress-note"><span className="spinner dark" /> Loading candidates…</div>;
  }

  // Candidates that failed to parse still get a Match row (scored 0 with the
  // parse error as the reason), but they belong in the Needs-attention panel
  // above, not mixed into the ranking where a 0% reads as a real judgement.
  // Wage vetoes are the exception — 0% there IS the judgement, so they stay.
  const scored = ranking.filter((r) =>
    ["success", "skipped_wage_veto"].includes(r.candidate?.parsing_status)
  );
  if (scored.length === 0) {
    return (
      <div className="empty">
        <div className="dropzone-icon">◔</div>
        <h3>No candidates scored yet</h3>
        <p>Upload résumés above and process them to see the ranking.</p>
      </div>
    );
  }

  return (
    <>
      <div className="toolbar">
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 600, fontSize: 24, color: "var(--ink)" }}>
          Ranked candidates
        </h2>
        <span style={{ color: "var(--slate)", fontSize: 14 }}>{scored.length} scored</span>
      </div>

      <div className="rank-list">
        {scored.map((row, i) => {
          const name = row.candidate?.full_name || row.candidate?.original_filename || "Unnamed candidate";
          const veto = row.score_breakdown?.wage_hard_fail || !row.score_breakdown?.scorable;
          const isOpen = openId === row.candidate.id;
          return (
            <div className={`rank-row ${isOpen ? "open" : ""}`} key={row.candidate.id}>
              <div className="rank-head" onClick={() => setOpenId(isOpen ? null : row.candidate.id)}>
                <div className="rank-num">{i + 1}</div>
                <div>
                  <div className="rank-name">{name}</div>
                  <div className="rank-sub">
                    {row.candidate.total_work_experience_years != null && (
                      <span>{row.candidate.total_work_experience_years}y total</span>
                    )}
                    {row.score_breakdown?.experience?.relevant_years != null && (
                      <span>{row.score_breakdown.experience.relevant_years.toFixed(1)}y relevant</span>
                    )}
                    {row.candidate.education_level && <span>{row.candidate.education_level}</span>}
                    {row.candidate.detected_language && <span>{row.candidate.detected_language.toUpperCase()}</span>}
                  </div>
                </div>
                <div className={`score-badge ${veto ? "veto" : ""}`}>
                  <span className="score-num">{Math.round(row.total_score)}</span>
                  <span className="score-max">/100</span>
                </div>
                <button
                  className="row-recompute"
                  onClick={(e) => handleRecompute(e, row.candidate.id)}
                  disabled={rescoringId === row.candidate.id}
                  title="Recompute this candidate's score"
                >
                  {rescoringId === row.candidate.id ? <span className="spinner dark" /> : "Recompute"}
                </button>
                <button
                  className="row-remove"
                  onClick={(e) => handleRemove(e, row.candidate.id, name)}
                  disabled={removingId === row.candidate.id}
                  title="Remove candidate"
                >
                  {removingId === row.candidate.id ? <span className="spinner dark" /> : "Remove"}
                </button>
                <span className="chev">▾</span>
              </div>
              {isOpen && (
                <>
                  <div className="contact-block">
                    <div className="contact-item">
                      <span className="contact-label">Email</span>
                      <span className="contact-value">
                        {row.candidate.email
                          ? <a href={`mailto:${row.candidate.email}`}>{row.candidate.email}</a>
                          : <span className="contact-empty">—</span>}
                      </span>
                    </div>
                    <div className="contact-item">
                      <span className="contact-label">Phone</span>
                      <span className="contact-value">
                        {row.candidate.phone
                          ? <a href={`tel:${row.candidate.phone}`}>{row.candidate.phone}</a>
                          : <span className="contact-empty">—</span>}
                      </span>
                    </div>
                  </div>
                  <ScoreBreakdown
                    breakdown={row.score_breakdown}
                    vacancyId={vacancyId}
                    candidateId={row.candidate.id}
                    onWageSaved={onChanged}
                  />
                </>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
