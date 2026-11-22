import fs from "fs"
import path from "path"
import type { NextConfig } from "next"

// Mock data: prefer the gitignored local file, fall back to the committed example.
const mockDir = path.join(process.cwd(), "src", "mock")
const localResearch = path.join(mockDir, "research.local.ts")
const researchMock = fs.existsSync(localResearch)
  ? localResearch
  : path.join(mockDir, "research.example.ts")

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    serverActions: {
      bodySizeLimit: "512mb",
    },
  },
  webpack(config) {
    config.resolve.alias = { ...config.resolve.alias, "@mock/research": researchMock }
    return config
  },
}

export default nextConfig
