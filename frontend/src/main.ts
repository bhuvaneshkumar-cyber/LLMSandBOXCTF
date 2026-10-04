// ─────────────────────────────────────────────────────────────────────────────
// main.ts — Entry point: the session flow, gluing the API, the UI and the 3D vault.
// ─────────────────────────────────────────────────────────────────────────────

import "./styles.css";
import { ApiError, api } from "./api";
import { session } from "./participant";
import type { ChatState } from "./types";
import * as ui from "./ui";
import type { Layout, Vault } from "./vault";

const idle = () => {};
let vault: Vault = { frame: idle, thinking: idle, speak: idle, keystroke: idle, alarm: idle, breach: idle, seal: idle };
let layout: Layout = "gate";
let state: ChatState | null = null;

// three.js is the heaviest chunk, so it loads after the UI is usable; until then scene calls are no-ops.
import("./vault").then(({ createVault }) => {
  const scene = createVault(document.querySelector("#vault") as HTMLCanvasElement);
  if (!scene) return document.body.classList.add("no-webgl");
  vault = scene;
  vault.frame(layout);
  if (state?.solved) vault.breach(true);
});

const expired = (error: unknown) => error instanceof ApiError && error.status === 401;

function showGate(message = ""): void {
  session.clear();
  state = null;
  layout = "gate";
  vault.seal();
  vault.frame(layout);
  ui.showGate(message);
}

function enter(next: ChatState): void {
  state = next;
  layout = "console";
  ui.showConsole(next);
  vault.frame(layout);
  if (next.solved) vault.breach(true);
}

function fail(error: unknown): void {
  if (expired(error)) return showGate("Your session expired. Log in again.");
  vault.alarm();
  ui.toast(error);
}

async function refresh(): Promise<void> {
  try {
    state = await api.state();
    ui.setCharges(state.remaining, state.limit);
  } catch (error) {
    fail(error);
  }
}

async function send(text: string): Promise<void> {
  if (!state) return;
  const turn = ui.pendingTurn(text);
  ui.setBusy(true);
  vault.thinking(true);
  try {
    const result = await api.send(text);
    vault.thinking(false);
    vault.speak(ui.resolveTurn(turn, result.reply));
    state.remaining = result.remaining;
    ui.setCharges(result.remaining, state.limit);
    if (result.solved && !state.solved) {
      state.solved = true;
      vault.breach();
      ui.showBreach(result.reply, state.username);
    }
  } catch (error) {
    vault.thinking(false);
    ui.rejectTurn(turn);
    ui.restorePrompt(text);
    if (error instanceof ApiError && error.status === 429 && error.retryAfter) ui.recharge(error.retryAfter, refresh);
    fail(error);
  } finally {
    ui.setBusy(false);
  }
}

ui.onAuth(async (mode, username, password) => {
  session.set((await api[mode](username, password)).token);
  const next = await api.state();
  await ui.leaveGate();
  enter(next);
});
ui.onSend(send);
ui.onType(() => vault.keystroke());
ui.onNewChat(async () => {
  try {
    await api.reset();
    await ui.clearLog();
    vault.speak(500);
  } catch (error) {
    fail(error);
  }
});
ui.onLeave(() => showGate());

if (!session.token()) {
  showGate();
} else {
  api.state().then(enter, (error: Error) => showGate(expired(error) ? "Your session expired. Log in again." : error.message));
}
