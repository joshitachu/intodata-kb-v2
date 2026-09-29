import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // MCP REST API: `python -m mcp_server --serve` and compose both publish 8002.
      "/mcp": "http://localhost:8002",
      "/github": "http://localhost:8002",
      "/health": "http://localhost:8002",
      "/logs": "http://localhost:8002",
      "/mc": {
        target: "http://localhost:9100",
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/mc/, ""),
      },
      "/auth": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/auth/, "/api/v1/auth"),
      },
      "/nexus-api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/nexus-api/, "/api/v1"),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/tests/setup.ts"],
    include: ["src/tests/**/*.test.{ts,tsx}"],
  },
});
