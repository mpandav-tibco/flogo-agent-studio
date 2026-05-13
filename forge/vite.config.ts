import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 7025,
    proxy: {
      // Deploy/export endpoints must come before the /api/v1 catch-all
      "^/api/v1/agents/[^/]+/deploy": {
        target: "http://localhost:7030",
        changeOrigin: true,
      },
      "^/api/v1/agents/[^/]+/export": {
        target: "http://localhost:7030",
        changeOrigin: true,
      },
      // Design service
      "/api/v1": {
        target: "http://localhost:7020",
        changeOrigin: true,
      },
      // Agent builder service
      "/api/agent-builder": {
        target: "http://localhost:7010",
        changeOrigin: true,
      },
      // Feedback service
      "/api/feedback": {
        target: "http://localhost:7003",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
