// ─────────────────────────────────────────────────────────────────────────────
// participant.ts — Holds the participant's login token.
//
// Persisted to localStorage, wrapped in try/catch: a restricted context
// (private browsing edge cases, storage disabled) falls back to in-memory
// only, which just means logging in again after a refresh — never fatal.
// ─────────────────────────────────────────────────────────────────────────────

const TOKEN_KEY = "vault-keeper:token";

let token: string | null = null;
try {
  token = localStorage.getItem(TOKEN_KEY);
} catch {
  // Restricted context — memory only.
}

export const session = {
  token: () => token,
  set(value: string) {
    token = value;
    try {
      localStorage.setItem(TOKEN_KEY, value);
    } catch {
      // Restricted context — memory only.
    }
  },
  clear() {
    token = null;
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      // Restricted context — memory only.
    }
  },
};
