import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import * as http from "http";

export default defineConfig({
  plugins: [
    react(),
    {
      // Vite 5's proxy does not support a `router` function for dynamic target
      // selection — the option is silently ignored and requests always go to
      // the static `target`.  Instead, handle /api/agent-runtime/{port}/...
      // with a custom Connect middleware that manually proxies to the correct
      // per-agent service port.
      name: "per-agent-proxy",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const m = req.url?.match(/^\/api\/agent-runtime\/(\d+)(\/.*)/);
          if (!m) return next();
          const [, portStr, path] = m;
          const proxyReq = http.request(
            {
              hostname: "127.0.0.1",
              port: parseInt(portStr, 10),
              path,
              method: req.method,
              headers: { ...req.headers, host: `127.0.0.1:${portStr}` },
            },
            (proxyRes) => {
              res.writeHead(proxyRes.statusCode!, proxyRes.headers as http.OutgoingHttpHeaders);
              proxyRes.pipe(res, { end: true });
            },
          );
          proxyReq.on("error", (e) => {
            res.statusCode = 502;
            res.end(`Proxy error: ${e.message}`);
          });
          req.pipe(proxyReq, { end: true });
        });
      },
    },
  ],
  server: {
    port: 7025,
    host: "127.0.0.1",
    proxy: {
      // Runtime manager (deployment.py) — docker-deploy and agent runtime status
      "/api/runtime": {
        target: "http://127.0.0.1:7050",
        changeOrigin: true,
      },
      // Admin console — platform service health + agent process list
      "/api/admin": {
        target: "http://127.0.0.1:7050",
        changeOrigin: true,
      },
      // Deploy/export endpoints — handled by Python runtime manager (deploy-service retired)
      "^/api/v1/agents/[^/]+/deploy": {
        target: "http://127.0.0.1:7050",
        changeOrigin: true,
      },
      "^/api/v1/agents/[^/]+/export": {
        target: "http://127.0.0.1:7050",
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
      // Feedback service — merged into platform-service (port 7020)
      "/api/feedback": {
        target: "http://localhost:7020",
        changeOrigin: true,
      },
      // Ingestion service (standalone)
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
