// ─────────────────────────────────────────────────────────────────────────────
// participant.ts — Responsible for obtaining and holding the participant ID.
// Does NOT touch localStorage (may run in restricted contexts).
// ─────────────────────────────────────────────────────────────────────────────

let _participantId: string | null = null;

/**
 * Prompt the user for their participant ID and store it in-memory.
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
