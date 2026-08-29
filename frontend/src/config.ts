// ─────────────────────────────────────────────────────────────────────────────
// config.ts — Application configuration constants.
//
// In development, leave BASE_URL as "/api/v1" — the Vite dev proxy will
// forward all /api/* requests to http://localhost:8000, so there are no
// cross-origin preflight requests.
//
// For production (Render / deployed backend), change BASE_URL to the full URL:
//   export const BASE_URL = "https://llm-sandbox.onrender.com/api/v1";
// ─────────────────────────────────────────────────────────────────────────────

export const BASE_URL = import.meta.env.PROD 
  ? "https://sandboxllm.onrender.com/api/v1" 
  : "http://localhost:8000/api/v1";
