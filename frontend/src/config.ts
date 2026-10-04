// ─────────────────────────────────────────────────────────────────────────────
// config.ts — API base path. Same origin everywhere: FastAPI serves the built
// frontend in production, and the Vite dev server proxies /api in development.
// ─────────────────────────────────────────────────────────────────────────────

export const BASE_URL = "/api/v1";
