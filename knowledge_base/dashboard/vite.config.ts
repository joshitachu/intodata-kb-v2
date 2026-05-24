import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/mcp": "http://localhost:8001",
      "/github": "http://localhost:8001",
      "/health": "http://localhost:8001",
      "/logs": "http://localhost:8001",
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
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/tests/setup.ts"],
    include: ["src/tests/**/*.test.{ts,tsx}"],
  },
});
