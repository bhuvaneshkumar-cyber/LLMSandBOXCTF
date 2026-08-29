// ─────────────────────────────────────────────────────────────────────────────
// types.ts — Shared TypeScript interfaces matching the FastAPI schema.
// ─────────────────────────────────────────────────────────────────────────────

/** Body sent to POST /api/v1/chat */
export interface ChatRequest {
  participant_id: string;
  prompt: string;
  provider_api_key?: string | null;
}

/** Response returned by POST /api/v1/chat */
export interface ChatResponse {
  response: string;
  solved: boolean;
  participant_token: string;
}

/** A single rendered message in the UI */
export type MessageRole = "user" | "ai";
