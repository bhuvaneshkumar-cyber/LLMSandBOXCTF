// ─────────────────────────────────────────────────────────────────────────────
// types.ts — Shapes of the FastAPI responses (app/api/routes_chat.py).
// ─────────────────────────────────────────────────────────────────────────────

export interface Turn {
  prompt: string;
  reply: string;
}

/** GET /api/v1/chat — everything the console needs after login or a reload. */
export interface ChatState {
  username: string;
  solved: boolean;
  remaining: number;
  limit: number;
  memory: number; // how many of the latest turns the Keeper can still see
  turns: Turn[];
}

/** POST /api/v1/chat */
export interface Reply {
  reply: string;
  solved: boolean;
  remaining: number;
}
