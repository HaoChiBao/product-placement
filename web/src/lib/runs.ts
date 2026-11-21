import { randomUUID } from "crypto"
import fs from "fs"
import path from "path"

export type Region = { x: number; y: number; w: number; h: number }

export type Moment = {
  start_s: number
  end_s: number
  frame: string
  frame_w: number
  frame_h: number
  region: Region
  whats_there: string
  score: number
  reason: string
  frameUrl?: string
  clipUrl?: string
}

export type SuggestRun = {
  runId: string
  video: string
  n: number
  window_s: number
  duration_s: number
  elapsed_s?: number
  moments: Moment[]
  contact_sheet?: string
}

export function repoRoot(): string {
  // web/ is one level under the repo root
  return path.resolve(process.cwd(), "..")
}

export function runsRoot(): string {
  const root = path.join(repoRoot(), "runs", "ui")
  fs.mkdirSync(root, { recursive: true })
  return root
}

export function newRunId(): string {
  return randomUUID().slice(0, 8)
}

export function runDir(runId: string): string {
  return path.join(runsRoot(), runId)
}

export function pythonBin(): string {
  const venv = path.join(repoRoot(), ".venv", "bin", "python")
  if (fs.existsSync(venv)) return venv
  return "python3"
}

export function withUrls(run: SuggestRun): SuggestRun {
  return {
    ...run,
    moments: run.moments.map((m, i) => {
      const name = path.basename(m.frame) || `moment_${String(i + 1).padStart(2, "0")}.jpg`
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
