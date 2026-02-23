import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/mcp": "http://localhost:8001",
      "/github": "http://localhost:8001",
      "/health": "http://localhost:8001",
      "/logs": "http://localhost:8001",
    },
  },
});
