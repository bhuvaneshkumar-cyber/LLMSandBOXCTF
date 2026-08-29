// ─────────────────────────────────────────────────────────────────────────────
// participant.ts — Responsible for obtaining and holding the participant ID
// and its ownership token (see security.py verify_participant_token).
//
// Persisted to localStorage, wrapped in try/catch: a restricted context
// (private browsing edge cases, storage disabled) falls back to in-memory
// only, which just means the ID prompt reappears on refresh — never fatal.
// ─────────────────────────────────────────────────────────────────────────────

const ID_KEY = "llm-sandbox:participant-id";
const TOKEN_KEY = "llm-sandbox:participant-token";

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Restricted context — silently stay in-memory-only for this key.
  }
}

let _participantId: string | null = readStorage(ID_KEY);
let _participantToken: string | null = readStorage(TOKEN_KEY);

/**
 * Return the stored participant ID, or prompt for one and persist it.
 * Keeps re-prompting until a non-empty value is supplied.
 */
export function initParticipant(): string {
  while (!_participantId) {
    const input = window.prompt(
      "Welcome to the LLM Sandbox CTF!\n\nEnter your Participant ID (e.g. team-name or email):"
    );
    const trimmed = (input ?? "").trim();
    if (trimmed) {
      _participantId = trimmed;
      writeStorage(ID_KEY, trimmed);
    }
  }
  return _participantId;
}

/** Return the current participant ID (must call initParticipant first). */
export function getParticipantId(): string {
  if (!_participantId) {
    throw new Error("Participant ID has not been initialised.");
  }
  return _participantId;
}

/** Return the stored ownership token, or null if this ID has never chatted yet. */
export function getParticipantToken(): string | null {
  return _participantToken;
}

/** Store the ownership token returned by the backend on each /chat response. */
export function setParticipantToken(token: string): void {
  _participantToken = token;
  writeStorage(TOKEN_KEY, token);
}
