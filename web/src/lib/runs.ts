import { randomUUID } from "crypto"
import fs from "fs"
import path from "path"
import { spawn } from "child_process"

import type { JobStatus, SuggestRun } from "@/lib/types"

export type { JobStatus, Moment, Region, SuggestRun } from "@/lib/types"
export {
  SEEDANCE_USD_PER_S,
  MAX_SWAP_INPUT_S,
  WAN_720P_USD_PER_10S,
  estimateSwapCostUsd,
  estimateWanSwapCostUsd,
} from "@/lib/types"

export function repoRoot(): string {
  const env = (process.env.REPO_ROOT || "").trim()
  if (env) return path.resolve(env)
  // web/ is one level under the repo root in local/dev; Docker sets REPO_ROOT=/app
  return path.resolve(process.cwd(), "..")
}

export function dataRoot(): string {
  const env = (process.env.DATA_DIR || "").trim()
  const root = env ? path.resolve(env) : path.join(repoRoot(), "runs", "ui")
  fs.mkdirSync(root, { recursive: true })
  return root
}

export function runsRoot(): string {
  // Suggest runs live under DATA_DIR/jobs (same as swap jobs).
  const root = path.join(dataRoot(), "jobs")
  fs.mkdirSync(root, { recursive: true })
  return root
}

export function newRunId(): string {
  return randomUUID().slice(0, 12)
}

export function runDir(runId: string): string {
  return path.join(runsRoot(), runId)
}

export function pythonBin(): string {
  const env = (process.env.PYTHON_BIN || "").trim()
  if (env) return env
  // Prefer system/interpreter on PATH — do not require the repo .venv.
  // Windows venvs put the interpreter in Scripts\python.exe, not bin/python.
  const win = process.platform === "win32"
  const venv = win
    ? path.join(repoRoot(), ".venv", "Scripts", "python.exe")
    : path.join(repoRoot(), ".venv", "bin", "python")
  if (process.env.NODE_ENV !== "production" && fs.existsSync(venv)) return venv
  return win ? "python" : "python3"
}

export function pythonEnv(): NodeJS.ProcessEnv {
  const src = path.join(repoRoot(), "src")
  const existing = process.env.PYTHONPATH || ""
  const pythonpath = existing ? `${src}${path.delimiter}${existing}` : src
  return {
    ...process.env,
    PYTHONPATH: pythonpath,
    DATA_DIR: dataRoot(),
    MODEL_CACHE_DIR:
      process.env.MODEL_CACHE_DIR ||
      (fs.existsSync("/app/models")
        ? "/app/models"
        : path.join(repoRoot(), "checkpoints")),
  }
}

export function withUrls(run: SuggestRun): SuggestRun {
  return {
    ...run,
    moments: run.moments.map((m, i) => {
      const name =
        path.basename(m.frame) || `moment_${String(i + 1).padStart(2, "0")}.jpg`
      return {
        ...m,
        frameUrl: `/api/runs/${run.runId}/frame/${encodeURIComponent(name)}`,
        clipUrl: `/api/runs/${run.runId}/clip?start=${m.start_s}&end=${m.end_s}`,
      }
    }),
  }
}

export function readRun(runId: string): SuggestRun | null {
  const metaPath = path.join(runDir(runId), "moments.json")
  if (!fs.existsSync(metaPath)) return null
  const raw = JSON.parse(fs.readFileSync(metaPath, "utf8")) as Omit<SuggestRun, "runId"> & {
    runId?: string
  }
  return withUrls({ ...raw, runId })
}

export function readJob(jobId: string): JobStatus | null {
  const p = path.join(runDir(jobId), "status.json")
  if (!fs.existsSync(p)) return null
  return JSON.parse(fs.readFileSync(p, "utf8")) as JobStatus
}

export function writeJob(jobId: string, status: JobStatus): void {
  const dir = runDir(jobId)
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, "status.json"), JSON.stringify(status, null, 2) + "\n")
}

function markJobFailed(jobId: string | undefined, error: string): void {
  if (!jobId) return
  try {
    const cur = readJob(jobId)
    if (!cur || cur.status === "completed" || cur.status === "failed") return
    writeJob(jobId, {
      ...cur,
      status: "failed",
      progress: "Failed",
      error,
      updated_at: new Date().toISOString(),
    })
  } catch {
    // best-effort — UI poll timeout is the backstop
  }
}

/** Spawn a detached Python job worker; returns immediately. */
export function spawnJob(
  args: string[],
  opts?: { cwd?: string; jobId?: string }
): void {
  const jobId = opts?.jobId
  let child
  try {
    child = spawn(pythonBin(), args, {
      cwd: opts?.cwd || repoRoot(),
      env: pythonEnv(),
      detached: true,
      stdio: ["ignore", "ignore", "pipe"],
    })
  } catch (err) {
    markJobFailed(jobId, err instanceof Error ? err.message : String(err))
    throw err
  }

  let stderr = ""
  child.stderr?.on("data", (chunk: Buffer | string) => {
    stderr += chunk.toString()
    if (stderr.length > 8000) stderr = stderr.slice(-4000)
  })
  child.on("error", (err) => {
    markJobFailed(jobId, err.message)
  })
  child.on("exit", (code, signal) => {
    // Normal success: code 0. Signal kills (OOM/SIGTERM/SIGSEGV) report code=null.
    if (code === 0) return
    if (code === null && !signal) return
    const detail =
      stderr.trim().slice(-800) ||
      (signal
        ? `worker killed by signal ${signal}`
        : `worker exited with code ${code}`)
    markJobFailed(jobId, detail)
  })
  child.unref()
}
