import { useState } from "react";
import * as api from "./api.js";

export const CURRENCIES = ["AZN", "USD", "EUR", "RUB", "TRY"];

/**
 * Inline editor for a candidate's wage expectation. Used in two places:
 *  - the upload results list, right after a résumé is parsed (so a recruiter
 *    who already knows the expectation - e.g. from a phone screen - can
 *    record it immediately, without a detour through the ranking view)
 *  - the score breakdown, for correcting it later
 *
 * Saving does two things in sequence: persists the wage, then rescores just
 * this one candidate so the number shown is never stale. `disabled` is used
 * to hold off editing during upload's own scoring pass, so this never races
 * the batch score_vacancy_candidates call for the same candidate.
 */
export default function WageEditor({
  vacancyId,
  candidateId,
  initialWage = null,
  initialCurrency = "AZN",
  onSaved,
  disabled = false,
  compact = false,
}) {
  const [value, setValue] = useState(initialWage ?? "");
  const [currency, setCurrency] = useState(initialCurrency || "AZN");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null); // { kind: "success"|"error", text }

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const numeric = value === "" ? null : Number(value);
      await api.setCandidateWage(candidateId, numeric, currency);
      await api.scoreSingleCandidate(vacancyId, candidateId);
      setMessage({ kind: "success", text: compact ? "Saved" : "Saved — score updated" });
      onSaved && onSaved();
    } catch (err) {
      setMessage({ kind: "error", text: err.message || "Could not save" });
    } finally {
      setSaving(false);
    }
  }

  const locked = disabled || saving;

  return (
    <div className={`wage-edit ${compact ? "compact" : ""}`}>
      <input
        type="number"
        min="0"
        placeholder="Wage expectation"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        disabled={locked}
      />
      <select value={currency} onChange={(e) => setCurrency(e.target.value)} disabled={locked}>
        {CURRENCIES.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
      <button className="btn btn-ghost btn-sm" onClick={save} disabled={locked}>
        {saving ? <span className="spinner dark" /> : "Save"}
      </button>
      {message && <span className={`wage-msg ${message.kind}`}>{message.text}</span>}
      {!message && disabled && <span className="wage-msg muted">Available once processing finishes</span>}
    </div>
  );
}
