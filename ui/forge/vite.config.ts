import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 7025,
    host: "127.0.0.1",
    proxy: {
      // Runtime manager (deployment.py) — docker-deploy and agent runtime status
      "/api/runtime": {
        target: "http://localhost:7050",
        changeOrigin: true,
      },
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
      // Ingestion service
      "/api/ingest": {
        target: "http://localhost:7002",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
