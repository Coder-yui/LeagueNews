import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Keep `next build` from replacing assets used by a running dev server.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  outputFileTracingRoot: path.join(__dirname, "../.."),
  eslint: {
    ignoreDuringBuilds: true,
  },
  async rewrites() {
    const backend = (
      process.env.INTERNAL_API_URL ??
      process.env.NEXT_PUBLIC_API_URL ??
      "http://localhost:8000/api/v1"
    ).replace(/\/$/, "");
    return [{ source: "/api/v1/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
