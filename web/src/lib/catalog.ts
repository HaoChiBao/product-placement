import { spawnSync } from "child_process"
import fs from "fs"
import path from "path"

import { dataRoot, pythonBin, pythonEnv, repoRoot, spawnJob } from "@/lib/runs"

/** Parse CLI stdout; tolerate leading log lines if any leaked onto stdout. */
function parseCatalogStdout(text: string, stderr: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch (first) {
    const lines = trimmed.split(/\r?\n/)
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]
      if (!line.startsWith("{") && !line.startsWith("[")) continue
      try {
        return JSON.parse(lines.slice(i).join("\n"))
      } catch {
        /* keep scanning */
      }
    }
    const detail = (stderr || String(first)).trim().slice(-2000)
    throw new Error(detail || "catalog stdout was not valid JSON")
  }
}

export function catalogCmd(args: string[]): unknown {
  const res = spawnSync(pythonBin(), ["-m", "product_placement.catalog", ...args], {
    cwd: repoRoot(),
    env: pythonEnv(),
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  })
  if (res.status !== 0) {
    const err = (res.stderr || res.stdout || "catalog command failed").trim()
    throw new Error(err.slice(-2000))
  }
  return parseCatalogStdout(res.stdout || "", res.stderr || "")
}

export function ensureCatalogDb(): void {
  catalogCmd(["init-db"])
}

/** Non-blocking: wake the import worker and kick a locked one-shot if needed. */
export function wakeMoviesImport(): { wake: boolean; spawned: boolean } {
  const wakePath = path.join(dataRoot(), "import-movies.wake")
  fs.mkdirSync(path.dirname(wakePath), { recursive: true })
  fs.writeFileSync(wakePath, new Date().toISOString() + "\n")
  // Detached one-shot covers local/dev when the docker worker is not running.
  // The flock makes concurrent worker + one-shot safe.
  spawnJob(["-m", "product_placement.catalog", "import-movies", "--non-blocking"], {})
  return { wake: true, spawned: true }
}

export function spawnEpisodeSuggest(episodeId: string): void {
  spawnJob(
    ["-m", "product_placement.jobs", "episode-suggest", "--episode-id", episodeId],
    {}
  )
}

/** Re-run the moment finder for one episode with this brand in the prompt. */
export function spawnBrandScan(episodeId: string, brandId: string): void {
  spawnJob(
    [
      "-m",
      "product_placement.jobs",
      "brand-scan",
      "--episode-id",
      episodeId,
      "--brand-id",
      brandId,
    ],
    {}
  )
}

export function spawnPlacementSwap(
  placementId: string,
  opts?: { testMode?: boolean; confirm?: boolean; method?: "wan" | "higgsfield" }
): void {
  const testMode = opts?.testMode !== false
  const args = [
    "-m",
    "product_placement.jobs",
    "placement-swap",
    "--placement-id",
    placementId,
  ]
  if (testMode) args.push("--test-mode")
  else args.push("--no-test-mode")
  if (opts?.confirm !== false) args.push("--confirm")
  if (opts?.method) args.push("--method", opts.method)
  spawnJob(args, {})
}

export function localObjectPath(key: string): string {
  const cleaned = key.replace(/\.\./g, "_").replace(/^\/+/, "")
  return path.join(dataRoot(), "objects", cleaned)
}

export function localObjectExists(key: string): boolean {
  try {
    return fs.existsSync(localObjectPath(key))
  } catch {
    return false
  }
}

export function usingS3(): boolean {
  return Boolean(
    (process.env.AWS_S3_BUCKET_NAME || "").trim() &&
      (process.env.AWS_ACCESS_KEY_ID || "").trim() &&
      (process.env.AWS_SECRET_ACCESS_KEY || "").trim()
  )
}
