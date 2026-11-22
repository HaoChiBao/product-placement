import fs from "fs"
import path from "path"
import { parseEnv } from "util"
import type { NextConfig } from "next"

// API keys (TinyFish, OpenRouter, S3, ...) live in the repo-root .env, shared with the
// Python jobs. Load it so `npm run dev` / `npm start` see them. Variables already set
// (Railway, the shell) win, empty values are skipped, and a missing file is a no-op.
// (@next/env's loadEnvConfig can't do this: it caches the first directory it loaded.)
const rootEnv = path.join(process.cwd(), "..", ".env")
if (fs.existsSync(rootEnv)) {
  for (const [key, value] of Object.entries(parseEnv(fs.readFileSync(rootEnv, "utf8")))) {
    if (value && process.env[key] === undefined) process.env[key] = value
  }
}

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
