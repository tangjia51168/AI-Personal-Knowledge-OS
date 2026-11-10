import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  output: process.env.STANDALONE_BUILD === "1" ? "standalone" : undefined,
  outputFileTracingRoot: path.join(process.cwd(), "../.."),
  poweredByHeader: false,
};

export default nextConfig;
