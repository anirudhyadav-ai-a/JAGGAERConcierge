/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI backend. Override with API_TARGET=http://host:port npm run dev
const apiTarget = process.env.API_TARGET ?? "http://localhost:8000";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Dev server serves the app at /. The production build is served by FastAPI at /app/.
  base: command === "build" ? "/app/" : "/",
  server: {
    port: 5173,
    proxy: {
      "/v1": apiTarget,
      "/health": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
}));
