// ─────────────────────────────────────────────────────────────────────────────
// main.ts — Application entry point. Wires up modules and event listeners.
// ─────────────────────────────────────────────────────────────────────────────

import "./styles.css";
import {
  initParticipant,
  getParticipantId,
  getParticipantToken,
  setParticipantToken,
} from "./participant";
import { sendChat } from "./api";
import {
  setParticipantLabel,
  appendMessage,
  setLoading,
  showSolvedBanner,
  autoResizeTextarea,
  getPromptValue,
  clearPromptInput,
} from "./ui";

// ── Initialise participant ────────────────────────────────────────────────────

const participantId = initParticipant();
setParticipantLabel(participantId);

// ── Event: auto-resize textarea while typing ──────────────────────────────────

const promptInput = document.getElementById(
  "prompt-input"
) as HTMLTextAreaElement;
promptInput.addEventListener("input", autoResizeTextarea);

// ── Event: Enter to submit (Shift+Enter inserts newline) ─────────────────────

promptInput.addEventListener("keydown", (e: KeyboardEvent) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    document.getElementById("chat-form")?.dispatchEvent(new Event("submit"));
  }
});

// ── Event: form submit → call API ─────────────────────────────────────────────

const chatForm = document.getElementById("chat-form") as HTMLFormElement;

chatForm.addEventListener("submit", async (e: Event) => {
  e.preventDefault();

  const text = getPromptValue();
  if (!text) return;

  appendMessage("user", text);
  clearPromptInput();
  setLoading(true);

  try {
    const result = await sendChat(getParticipantId(), text, getParticipantToken());
    setParticipantToken(result.participant_token);
    appendMessage("ai", result.response);

    if (result.solved) {
      showSolvedBanner();
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "Unknown error.";
    appendMessage("ai", `[System Error]: ${message}\nPlease try again.`);
  } finally {
    setLoading(false);
  }
});
