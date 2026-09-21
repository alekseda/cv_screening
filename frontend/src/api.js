// Thin wrapper around the FastAPI backend. The JWT is kept in memory and
// mirrored to sessionStorage so a page refresh doesn't force a re-login,
// while still clearing when the browser session ends.

const BASE = "/api/v1";

let token = sessionStorage.getItem("cvs_token") || null;

export function getToken() {
  return token;
}

export function setToken(value) {
  token = value;
  if (value) sessionStorage.setItem("cvs_token", value);
  else sessionStorage.removeItem("cvs_token");
}

function authHeaders(extra = {}) {
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function handle(res) {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // non-JSON error body, keep the status text
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export async function login(email, password) {
  // OAuth2 password flow expects form-encoded username/password.
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  const data = await handle(res);
  setToken(data.access_token);
  return data;
}

export async function register(email, password, fullName) {
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, full_name: fullName }),
  });
  return handle(res);
}

export async function me() {
  const res = await fetch(`${BASE}/users/me`, { headers: authHeaders() });
  return handle(res);
}

export async function listVacancies() {
  const res = await fetch(`${BASE}/vacancies`, { headers: authHeaders() });
  return handle(res);
}

export async function getVacancy(id) {
  const res = await fetch(`${BASE}/vacancies/${id}`, { headers: authHeaders() });
  return handle(res);
}

export async function listGlorriJobs() {
  const res = await fetch(`${BASE}/vacancies/glorri-jobs`, { headers: authHeaders() });
  return handle(res);
}

export async function createVacancy(payload) {
  const res = await fetch(`${BASE}/vacancies`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function deleteVacancy(id) {
  const res = await fetch(`${BASE}/vacancies/${id}`, { method: "DELETE", headers: authHeaders() });
  return handle(res);
}

export async function updateVacancy(id, payload) {
  const res = await fetch(`${BASE}/vacancies/${id}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return handle(res);
}

export async function uploadCandidates(vacancyId, files, wages) {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  // wages, if given, must be the same length/order as files - each entry
  // {wage, currency} lets the backend skip the entire parsing pipeline for a
  // candidate whose wage expectation already fails the vacancy's budget by
  // itself, rather than running it and then discovering that afterward.
  if (wages) form.append("wages_json", JSON.stringify(wages));
  const res = await fetch(`${BASE}/vacancies/${vacancyId}/candidates`, {
    method: "POST",
    headers: authHeaders(), // don't set Content-Type; browser sets the multipart boundary
    body: form,
  });
  return handle(res);
}

export async function deleteCandidate(candidateId) {
  const res = await fetch(`${BASE}/candidates/${candidateId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function scoreVacancy(vacancyId) {
  const res = await fetch(`${BASE}/vacancies/${vacancyId}/score`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function scoreSingleCandidate(vacancyId, candidateId) {
  // Rescores just this one candidate - used after editing their wage
  // expectation, so a change to one person doesn't require re-running the
  // whole vacancy.
  const res = await fetch(`${BASE}/vacancies/${vacancyId}/candidates/${candidateId}/score`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function getRanking(vacancyId) {
  const res = await fetch(`${BASE}/vacancies/${vacancyId}/ranking`, { headers: authHeaders() });
  return handle(res);
}

export async function setCandidateWage(candidateId, wage, currency) {
  const res = await fetch(`${BASE}/candidates/${candidateId}/wage`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ wage_expectation: wage, wage_currency: currency }),
  });
  return handle(res);
}

export async function listCandidates(vacancyId) {
  // Every candidate row for the vacancy, including ones that failed to parse
  // (the ranking endpoint only returns scored candidates, so failures would
  // otherwise be invisible after a page refresh).
  const res = await fetch(`${BASE}/vacancies/${vacancyId}/candidates`, { headers: authHeaders() });
  return handle(res);
}

export async function reparseFailed(vacancyId) {
  const res = await fetch(`${BASE}/vacancies/${vacancyId}/candidates/reparse-failed`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}

export async function syncGlorri(vacancyId) {
  const res = await fetch(`${BASE}/vacancies/${vacancyId}/sync-glorri`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle(res);
}
