import type { NextConfig } from "next";

// In production Caddy handles /mcp/* and /logs/* routing. In dev (no Caddy),
// we proxy them from the Next.js server so the browser can hit a single origin.
const MCP_URL = process.env.KB_API_URL || "http://localhost:8002";

const nextConfig: NextConfig = {
  output: "standalone",
  serverExternalPackages: [],
  experimental: {
    serverActions: {
      bodySizeLimit: "500mb",
    },
  },
  async rewrites() {
    return [
      { source: "/mcp/:path*", destination: `${MCP_URL}/mcp/:path*` },
      { source: "/logs/:path*", destination: `${MCP_URL}/logs/:path*` },
    ];
  },
};

export default nextConfig;
