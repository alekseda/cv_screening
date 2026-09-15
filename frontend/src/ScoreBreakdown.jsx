import WageEditor from "./WageEditor.jsx";

// The breakdown is the product's core promise: never a bare score. It shows
// how each component contributed, which requirements matched or are missing,
// whether experience/education/wage passed and why, and the per-job relevance
// the experience number was built from.

const COMPONENT_LABELS = {
  responsibilities: "Role match",
  wage: "Wage fit",
  experience: "Experience",
  required_skills: "Required skills",
  education: "Education",
  preferred_skills: "Preferred skills",
};

function pct(n) {
  return `${Math.round((n ?? 0) * 100)}%`;
}

export default function ScoreBreakdown({ breakdown, vacancyId, candidateId, onWageSaved }) {
  if (!breakdown) return null;

  if (!breakdown.scorable) {
    return (
      <div className="breakdown">
        <div className="veto-banner">{breakdown.reason}</div>
      </div>
    );
  }

  const b = breakdown;
  const components = b.components || {};
  const req = b.required_skills || {};
  const pref = b.preferred_skills || {};
  const exp = b.experience || {};
  const edu = b.education || {};
  const wage = b.wage || {};

  return (
    <div className="breakdown">
      {b.wage_hard_fail && (
        <div className="veto-banner">
          Scored 0 overall — {wage.reason}
        </div>
      )}

      {/* Component contributions */}
      <div className="comp-grid">
        {Object.entries(components).map(([key, c]) => (
          <div className="comp" key={key}>
            <div className="comp-name">{COMPONENT_LABELS[key] || key}</div>
            <div className="comp-bar">
              <span style={{ width: pct(c.score) }} />
            </div>
            <div className="comp-val">
              <b>{pct(c.score)}</b> · weight {Math.round((c.weight ?? 0) * 100)}%
            </div>
          </div>
        ))}
      </div>

      {/* Required skills */}
      <div className="bd-section">
        <h4>Required skills</h4>
        {(req.matched || []).map((s) => (
          <span className="tag tag-good" key={`rm-${s}`}>✓ {s}</span>
        ))}
        {(req.missing || []).map((s) => (
          <span className="tag tag-bad" key={`rx-${s}`}>✗ {s}</span>
        ))}
        {(req.not_scored_here || []).map((s) => (
          <span className="tag tag-muted" key={`rn-${s}`}>{s} · scored elsewhere</span>
        ))}
        {(req.matched || []).length === 0 &&
          (req.missing || []).length === 0 &&
          (req.not_scored_here || []).length === 0 && (
            <span className="comp-val">None specified</span>
          )}
      </div>

      {/* Preferred skills */}
      {((pref.matched || []).length > 0 || (pref.missing || []).length > 0) && (
        <div className="bd-section">
          <h4>Preferred skills</h4>
          {(pref.matched || []).map((s) => (
            <span className="tag tag-good" key={`pm-${s}`}>✓ {s}</span>
          ))}
          {(pref.missing || []).map((s) => (
            <span className="tag tag-muted" key={`px-${s}`}>{s}</span>
          ))}
        </div>
      )}

      {/* Experience */}
      <div className="bd-section">
        <h4>Experience</h4>
        <div className="check-line">
          <span className={`check-icon ${exp.passed ? "pass" : "fail"}`}>{exp.passed ? "✓" : "✗"}</span>
          <span>{exp.reason}{exp.is_estimate ? " (estimate — some roles had no dates)" : ""}</span>
        </div>
        {(exp.per_job || []).length > 0 && (
          <div style={{ marginTop: 8 }}>
            {exp.per_job.map((j, i) => (
              <div className="jobline" key={i}>
                <span className="rel">{Math.round((j.relevance ?? 0) * 100)}% relevant</span>
                {" · "}
                {j.title || "Untitled role"}
                {j.company ? ` · ${j.company}` : ""}
                {j.counted === false && " · not counted (no dates)"}
                {j.relevance_reason && <div className="reason">{j.relevance_reason}</div>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Education */}
      <div className="bd-section">
        <h4>Education</h4>
        <div className="check-line">
          <span className={`check-icon ${edu.passed ? "pass" : "fail"}`}>{edu.passed ? "✓" : "✗"}</span>
          <span>{edu.reason}</span>
        </div>
      </div>

      {/* Wage */}
      <div className="bd-section">
        <h4>Wage fit</h4>
        <div className="check-line">
          <span className={`check-icon ${wage.status === "within_budget" ? "pass" : wage.hard_fail ? "fail" : ""}`}>
            {wage.status === "within_budget" ? "✓" : wage.hard_fail ? "✗" : "·"}
          </span>
          <span>{wage.reason}</span>
        </div>
        {vacancyId && candidateId && (
          <WageEditor
            vacancyId={vacancyId}
            candidateId={candidateId}
            initialWage={wage.candidate_wage_expectation}
            initialCurrency={wage.candidate_currency || wage.vacancy_currency || "AZN"}
            onSaved={onWageSaved}
          />
        )}
      </div>
    </div>
  );
}
