// ─────────────────────────────────────────────────────────────────────────────
// ui.ts — All DOM work and its motion (anime.js). No fetch, no app state here.
//
// Replies are untrusted model output: they only ever reach the page through
// textContent / text nodes, never innerHTML.
// ─────────────────────────────────────────────────────────────────────────────

import { animate, scrambleText, stagger } from "animejs";
import type { ChatState } from "./types";

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = <T extends HTMLElement = HTMLElement>(selector: string) => document.querySelector(selector) as T;

const hud = $("#hud");
const gate = $("#gate");
const authForm = $<HTMLFormElement>("#auth-form");
const username = $<HTMLInputElement>("#username");
const password = $<HTMLInputElement>("#password");
const authSubmit = $<HTMLButtonElement>("#auth-submit");
const formError = $("#form-error");
const consoleEl = $("#console");
const log = $("#log");
const chips = $("#chips");
const promptInput = $<HTMLTextAreaElement>("#prompt");
const sendButton = $<HTMLButtonElement>("#send");
const charCount = $("#count");
const meter = $("#charges");
const cells = $("#cells");
const chargesLabel = $("#charges-label");
const badge = $("#badge");
const handle = $("#handle");
const breach = $("#breach");
const breachFlag = $("#breach-flag");
const breachWho = $("#breach-who");
const toastEl = $("#toast");

const FLAG = /FLAG\{[^}\s]*\}/g;
const GLYPHS = "░▒▓█▀▄▌▐<>/\\|=+*#";
let memory = 5;
let busy = false;
let recharging = false;

// Split headline letters into spans once, so they can be staggered.
for (const line of document.querySelectorAll(".split")) {
  line.replaceChildren(
    ...[...(line.textContent ?? "")].map((char) => Object.assign(document.createElement("span"), { textContent: char })),
  );
}

function el(tag: string, className: string, text = ""): HTMLElement {
  return Object.assign(document.createElement(tag), { className, textContent: text });
}

/** Text with every FLAG{...} wrapped in a glowing <mark>, built from text nodes only. */
function highlighted(text: string): Node[] {
  const nodes: Node[] = [];
  let last = 0;
  for (const match of text.matchAll(FLAG)) {
    nodes.push(document.createTextNode(text.slice(last, match.index)), el("mark", "flag", match[0]));
    last = match.index + match[0].length;
  }
  nodes.push(document.createTextNode(text.slice(last)));
  return nodes;
}

const errorText = (error: unknown) => (error instanceof Error ? error.message : "Something went wrong.");

// ── Gate ─────────────────────────────────────────────────────────────────────

export function showGate(message = ""): void {
  hud.hidden = consoleEl.hidden = breach.hidden = true;
  gate.hidden = false;
  gate.removeAttribute("style");
  formError.textContent = message;
  if (reducedMotion) return;
  animate(".wordmark .split span", {
    opacity: [0, 1],
    y: ["0.5em", "0em"],
    filter: ["blur(12px)", "blur(0px)"],
    delay: stagger(45),
    duration: 900,
    ease: "outExpo",
  });
  animate(".gate .reveal", { opacity: [0, 1], y: [24, 0], delay: stagger(120, { start: 350 }), duration: 900, ease: "outExpo" });
}

export function onAuth(handler: (mode: "login" | "register", username: string, password: string) => Promise<void>): void {
  let mode: "login" | "register" = "login";
  for (const tab of authForm.querySelectorAll<HTMLButtonElement>("[data-mode]")) {
    tab.addEventListener("click", () => {
      mode = tab.dataset.mode as typeof mode;
      for (const other of authForm.querySelectorAll("[data-mode]")) other.setAttribute("aria-selected", String(other === tab));
      password.autocomplete = mode === "login" ? "current-password" : "new-password";
      authSubmit.textContent = mode === "login" ? "Enter the vault" : "Enlist";
      formError.textContent = "";
    });
  }
  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    formError.textContent = "";
    authSubmit.disabled = true;
    try {
      await handler(mode, username.value, password.value);
      password.value = "";
    } catch (error) {
      formError.textContent = errorText(error);
      if (!reducedMotion) animate(authForm, { x: [0, -10, 9, -6, 4, 0], duration: 450, ease: "outQuad" });
    } finally {
      authSubmit.disabled = false;
    }
  });
}

export async function leaveGate(): Promise<void> {
  if (!reducedMotion) {
    await animate(gate, { opacity: 0, scale: 0.97, filter: ["blur(0px)", "blur(14px)"], duration: 550, ease: "inQuad" });
  }
  gate.hidden = true;
}

// ── Console ──────────────────────────────────────────────────────────────────

export function showConsole(state: ChatState): void {
  gate.hidden = true;
  hud.hidden = consoleEl.hidden = false;
  handle.textContent = state.username;
  badge.hidden = !state.solved;
  memory = state.memory;
  log.replaceChildren(...state.turns.map((turn) => turnElement(turn.prompt, turn.reply)));
  chips.hidden = state.turns.length > 0;
  markMemory();
  setCharges(state.remaining, state.limit);
  log.scrollTop = log.scrollHeight;
  promptInput.focus();
  if (reducedMotion) return;
  animate(hud, { opacity: [0, 1], y: [-16, 0], duration: 800, ease: "outExpo" });
  animate(consoleEl, { opacity: [0, 1], x: [48, 0], duration: 1000, ease: "outExpo" });
}

function turnElement(prompt: string, reply?: string): HTMLElement {
  const turn = el("div", "turn");
  const keeper = el("div", "msg keeper");
  if (reply === undefined) keeper.classList.add("pending");
  else keeper.append(...highlighted(reply));
  turn.append(el("p", "msg me", prompt), keeper);
  return turn;
}

/** Older turns fall out of the Keeper's context window; show that instead of hiding it. */
function markMemory(): void {
  const turns = [...log.children];
  turns.forEach((turn, i) => turn.classList.toggle("forgotten", i < turns.length - memory));
}

function followBottom(): void {
  if (log.scrollHeight - log.scrollTop - log.clientHeight < 120) log.scrollTop = log.scrollHeight;
}

export function pendingTurn(prompt: string): HTMLElement {
  chips.hidden = true;
  const turn = turnElement(prompt);
  log.append(turn);
  markMemory();
  log.scrollTop = log.scrollHeight;
  if (!reducedMotion) animate(turn.children, { opacity: [0, 1], y: [14, 0], delay: stagger(120), duration: 500, ease: "outExpo" });
  return turn;
}

/** Decrypts the reply into place. Returns how long that takes, so the orb can "speak" for as long. */
export function resolveTurn(turn: HTMLElement, reply: string): number {
  const bubble = turn.lastElementChild as HTMLElement;
  const visible = el("span", "");
  visible.setAttribute("aria-hidden", "true"); // screen readers get the plain text once, not the scramble
  bubble.classList.remove("pending");
  bubble.append(el("span", "sr-only", reply), visible);
  const settle = () => {
    visible.replaceChildren(...highlighted(reply));
    followBottom();
  };
  if (reducedMotion) {
    settle();
    return 0;
  }
  const duration = Math.min(1800, 350 + reply.length * 6);
  animate(visible, {
    textContent: scrambleText({ text: reply, chars: GLYPHS, duration }),
    onUpdate: followBottom,
    onComplete: settle,
  });
  return duration;
}

export function rejectTurn(turn: HTMLElement): void {
  const drop = () => {
    turn.remove();
    markMemory();
    chips.hidden = log.childElementCount > 0;
  };
  if (reducedMotion) return drop();
  animate(turn, { opacity: 0, x: 24, duration: 300, ease: "inQuad" }).then(drop);
}

export async function clearLog(): Promise<void> {
  const turns = [...log.children];
  if (turns.length && !reducedMotion) {
    await animate(turns, {
      opacity: 0,
      y: -12,
      filter: ["blur(0px)", "blur(6px)"],
      delay: stagger(25, { from: "last" }),
      duration: 350,
      ease: "inQuad",
    });
  }
  log.replaceChildren();
  chips.hidden = false;
}

// ── Composer ─────────────────────────────────────────────────────────────────

function fitPrompt(): void {
  promptInput.style.height = "auto";
  promptInput.style.height = `${promptInput.scrollHeight}px`;
  charCount.textContent = `${promptInput.value.length}/2000`;
}

function syncSend(): void {
  sendButton.disabled = busy || recharging;
}

export function setBusy(on: boolean): void {
  busy = on;
  syncSend();
}

export function onSend(handler: (text: string) => void): void {
  const submit = () => {
    const text = promptInput.value.trim();
    if (!text || sendButton.disabled) return;
    promptInput.value = "";
    fitPrompt();
    handler(text);
  };
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });
  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });
  chips.addEventListener("click", (event) => {
    const chip = (event.target as HTMLElement).closest("button");
    if (!chip) return;
    promptInput.value = chip.textContent ?? "";
    submit();
  });
}

export function onType(handler: () => void): void {
  promptInput.addEventListener("input", () => {
    fitPrompt();
    handler();
  });
}

/** Put a prompt that didn't go through back in the box, unless the user already typed something new. */
export function restorePrompt(text: string): void {
  if (promptInput.value) return;
  promptInput.value = text;
  fitPrompt();
}

export function onNewChat(handler: () => void): void {
  $("#new-chat").addEventListener("click", handler);
}

export function onLeave(handler: () => void): void {
  $("#leave").addEventListener("click", handler);
}

// ── Charges (the per-user rate limit, made visible) ──────────────────────────

export function setCharges(remaining: number, limit: number): void {
  const total = Math.min(limit, 20);
  if (cells.childElementCount !== total) {
    cells.replaceChildren(...Array.from({ length: total }, () => document.createElement("i")));
  }
  const lit = Math.ceil((remaining / limit) * total);
  [...cells.children].forEach((cell, i) => cell.classList.toggle("on", i < lit));
  if (!recharging) chargesLabel.textContent = `${remaining}/${limit}`;
  meter.setAttribute("aria-valuemax", String(limit));
  meter.setAttribute("aria-valuenow", String(remaining));
  const spent = cells.children[lit];
  if (spent && !reducedMotion) animate(spent, { scaleY: [1.9, 1], duration: 600, ease: "outElastic(1, .5)" });
}

let rechargeTimer = 0;

/** Count down a 429's Retry-After, then let the caller refresh the real numbers. */
export function recharge(seconds: number, onReady: () => void): void {
  const until = Date.now() + seconds * 1000;
  clearInterval(rechargeTimer);
  recharging = true;
  syncSend();
  const tick = () => {
    const left = Math.ceil((until - Date.now()) / 1000);
    if (left > 0) {
      chargesLabel.textContent = `recharging ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
      return;
    }
    clearInterval(rechargeTimer);
    recharging = false;
    syncSend();
    onReady();
  };
  tick();
  rechargeTimer = setInterval(tick, 1000);
}

// ── Notices ──────────────────────────────────────────────────────────────────

let toastTimer = 0;

export function toast(error: unknown): void {
  toastEl.textContent = errorText(error);
  toastEl.hidden = false;
  if (!reducedMotion) animate(toastEl, { opacity: [0, 1], y: [16, 0], duration: 400, ease: "outExpo" });
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastEl.hidden = true), 4500);
}

export function showBreach(reply: string, who: string): void {
  const flag = reply.match(FLAG)?.[0] ?? "";
  breach.hidden = badge.hidden = false;
  breachFlag.hidden = !flag;
  breachWho.textContent = `${who} talked the Keeper out of its secret.`;
  $("#breach-close").focus();
  if (reducedMotion) {
    breachFlag.textContent = flag;
    return;
  }
  breachFlag.textContent = "";
  animate(breach, { opacity: [0, 1], duration: 500 });
  animate("#breach-title .split span", {
    opacity: [0, 1],
    scale: [2.4, 1],
    filter: ["blur(16px)", "blur(0px)"],
    delay: stagger(55, { from: "center" }),
    duration: 1100,
    ease: "outExpo",
  });
  animate(breachFlag, { textContent: scrambleText({ text: flag, chars: GLYPHS, duration: 1800, delay: 600 }) });
  animate(badge, { scale: [0, 1], duration: 900, delay: 400, ease: "outElastic(1, .5)" });
}

$("#breach-close").addEventListener("click", () => {
  breach.hidden = true;
  promptInput.focus();
});
