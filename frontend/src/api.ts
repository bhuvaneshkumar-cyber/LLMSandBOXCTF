// ─────────────────────────────────────────────────────────────────────────────
// api.ts — All network communication with the FastAPI backend.
// ─────────────────────────────────────────────────────────────────────────────

import { BASE_URL } from "./config";
import type { ChatRequest, ChatResponse } from "./types";

/**
 * Send a chat message to the backend.
 * Throws an Error (with a user-friendly message) on non-2xx responses.
 */
export async function sendChat(
  participantId: string,
  prompt: string,
  providerApiKey?: string | null
): Promise<ChatResponse> {
  const body: ChatRequest = {
    participant_id: participantId,
    prompt,
    provider_api_key: providerApiKey ?? null,
  };

  const response = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await response.json();

  if (!response.ok) {
    // Surface the backend's `detail` field if present, otherwise a generic message.
    const detail: string =
      typeof data?.detail === "string"
        ? data.detail
        : `Server error: ${response.status}`;
    throw new Error(detail);
  }

  return data as ChatResponse;
}
