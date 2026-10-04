// ─────────────────────────────────────────────────────────────────────────────
// api.ts — All network communication with the FastAPI backend.
// ─────────────────────────────────────────────────────────────────────────────

import { BASE_URL } from "./config";
import { session } from "./participant";
import type { ChatState, Reply } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter = 0,
  ) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = session.token();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(BASE_URL + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError("Can't reach the vault. Check your connection.", 0);
  }
  if (response.status === 204) return undefined as T;

  // Render answers with an HTML page while a sleeping service wakes up, so JSON isn't guaranteed.
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(detail(data, response.status), response.status, Number(response.headers.get("Retry-After")) || 0);
  }
  return data as T;
}

function detail(data: { detail?: unknown }, status: number): string {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return String(data.detail[0]?.msg ?? "Check what you typed."); // FastAPI 422
  return status >= 500 ? "The vault is waking up. Try again in a few seconds." : `Request failed (${status}).`;
}

export const api = {
  login: (username: string, password: string) => call<{ token: string }>("POST", "/auth/login", { username, password }),
  register: (username: string, password: string) =>
    call<{ token: string }>("POST", "/auth/register", { username, password }),
  state: () => call<ChatState>("GET", "/chat"),
  send: (prompt: string) => call<Reply>("POST", "/chat", { prompt }),
  reset: () => call<void>("DELETE", "/chat"),
};
