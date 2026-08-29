import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  server: {
    port: 5173,
    open: true,
    // Proxy API calls to the FastAPI backend during development.
    // This makes ALL fetch("/api/v1/...") calls same-origin, so CORS
    // preflight requests never reach the backend at all.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        // Optional: uncomment to strip /api prefix if backend routes change
        // rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
