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
  images: {
    // Allow remote sources when a local media copy is missing and the
    // frontend falls back to the platform-originating image URL.
    remotePatterns: [
      // X (Twitter) CDN. Image requests are made directly from the browser
      // with ``unoptimized`` so reachability depends on the client's network.
      { protocol: "https", hostname: "pbs.twimg.com" },
      { protocol: "https", hostname: "video.twimg.com" },
      // Weibo / Sina image hosts used by the weibo connector.
      { protocol: "https", hostname: "*.sinaimg.cn" },
      // Tencent / LPL image hosts used by the tencent_lol connector.
      { protocol: "https", hostname: "*.tgl.qq.com" },
      { protocol: "https", hostname: "itea-stat.qq.com" },
      // Riot Games CDNs used by the riot_official connector.
      { protocol: "https", hostname: "am-a.akamaihd.net" },
      { protocol: "https", hostname: "cmsassets.rgpub.io" },
      { protocol: "https", hostname: "ddragon.leagueoflegends.com" },
      // Generic Akamai / Cloudfront fallbacks for brand assets.
      { protocol: "https", hostname: "*.cloudfront.net" },
      { protocol: "https", hostname: "*.akamaized.net" },
    ],
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
