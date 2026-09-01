// File: Frontend/src/api.js
// Central API base URL — override with VITE_API_URL in Frontend/.env
export const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function api(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}
