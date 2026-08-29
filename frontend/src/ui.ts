// ─────────────────────────────────────────────────────────────────────────────
// ui.ts — All DOM manipulation. No fetch, no business logic here.
// ─────────────────────────────────────────────────────────────────────────────

import type { MessageRole } from "./types";

// ── Element references (resolved once at import time) ────────────────────────

const chatHistory = document.getElementById("chat-history") as HTMLDivElement;
const loadingIndicator = document.getElementById(
  "loading-indicator"
) as HTMLDivElement;
const solvedBadge = document.getElementById("solved-badge") as HTMLDivElement;
const participantDisplay = document.getElementById(
  "participant-display"
) as HTMLParagraphElement;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
const promptInput = document.getElementById(
  "prompt-input"
) as HTMLTextAreaElement;

// ── Public API ────────────────────────────────────────────────────────────────

/** Render the participant ID in the header subtitle. */
export function setParticipantLabel(id: string): void {
  participantDisplay.textContent = `ID: ${id}`;
}

/**
 * Append a chat bubble to the history panel.
 * Scrolls to the bottom so the newest message is always visible.
 */
export function appendMessage(role: MessageRole, text: string): void {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role === "user" ? "user-message" : "ai-message"}`;

  const bubble = document.createElement("div");
  bubble.className = "message-content";
  bubble.textContent = text;

  wrapper.appendChild(bubble);
  chatHistory.appendChild(wrapper);
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

/** Toggle the loading indicator and disable/enable input controls. */
export function setLoading(isLoading: boolean): void {
  if (isLoading) {
    loadingIndicator.classList.remove("hidden");
    sendBtn.disabled = true;
    promptInput.disabled = true;
  } else {
    loadingIndicator.classList.add("hidden");
    sendBtn.disabled = false;
    promptInput.disabled = false;
    promptInput.focus();
  }
}

/** Show the "VAULT COMPROMISED" solved badge (idempotent). */
export function showSolvedBanner(): void {
  solvedBadge.classList.remove("hidden");
}

/** Auto-grow the textarea up to its CSS max-height. */
export function autoResizeTextarea(): void {
  promptInput.style.height = "auto";
  promptInput.style.height = `${promptInput.scrollHeight}px`;
}

/** Return the current trimmed prompt value. */
export function getPromptValue(): string {
  return promptInput.value.trim();
}

/** Clear the textarea and reset its height. */
export function clearPromptInput(): void {
  promptInput.value = "";
  promptInput.style.height = "auto";
}
