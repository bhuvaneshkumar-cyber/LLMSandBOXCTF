import { defineConfig } from "vite";

export default defineConfig({
  server: {
    open: true,
    // Dev only: keeps fetch("/api/...") same-origin by forwarding it to the FastAPI backend.
    proxy: { "/api": "http://localhost:8000" },
  },
  // The only big chunk is three.js (~140 kB gzip), already split off and loaded after the UI.
  build: { chunkSizeWarningLimit: 600 },
});
