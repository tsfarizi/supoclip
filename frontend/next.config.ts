import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  // Skip ESLint during builds (generated Prisma code causes lint errors)
  eslint: {
    ignoreDuringBuilds: true,
  },
  // Skip TypeScript errors during builds for now
  typescript: {
    ignoreBuildErrors: false,
  },
  // Node 22 + webpack 5 wasm hash crash ("Cannot read properties of undefined
  // (reading 'length')" at WasmHash._updateWithBuffer). Run webpack in-process
  // instead of the worker thread so `next build` is deterministic on Windows.
  experimental: {
    webpackBuildWorker: false,
  },
  webpack: (config) => {
    config.experiments = { ...config.experiments, asyncWebAssembly: false };
    return config;
  },
  async rewrites() {
    return [
      {
        source: "/js/script.js",
        destination: "https://datafa.st/js/script.js",
      },
      {
        source: "/api/events",
        destination: "https://datafa.st/api/events",
      },
    ];
  },
};

export default nextConfig;
