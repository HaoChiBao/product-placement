import { spawn } from "child_process"
import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import {
  newRunId,
  pythonBin,
  readRun,
  repoRoot,
  runDir,
  withUrls,
  type SuggestRun,
} from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 600

function runSuggest(videoPath: string, outDir: string, n: number): Promise<{
  elapsed_s: number
  stderr: string
}> {
  return new Promise((resolve, reject) => {
    const t0 = Date.now()
    const py = pythonBin()
    const args = [
      "-m",
      "product_placement",
      "suggest",
      "--video",
      videoPath,
      "-n",
      String(n),
      "--out-dir",
      outDir,
    ]
    const child = spawn(py, args, {
      cwd: repoRoot(),
      env: { ...process.env, PYTHONPATH: path.join(repoRoot(), "src") },
    })
    let stderr = ""
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString()
    })
    child.on("error", reject)
    child.on("close", (code) => {
      const elapsed_s = (Date.now() - t0) / 1000
      if (code !== 0) {
        reject(new Error(stderr.trim() || `suggest exited ${code}`))
        return
      }
      resolve({ elapsed_s, stderr })
    })
  })
}

export async function POST(req: NextRequest) {
  const runId = newRunId()
  const out = runDir(runId)
  fs.mkdirSync(path.join(out, "frames"), { recursive: true })

  try {
    const contentType = req.headers.get("content-type") || ""
    let videoPath = ""
    let n = 5

    if (contentType.includes("multipart/form-data")) {
      const form = await req.formData()
      n = Math.max(3, Math.min(5, Number(form.get("n") || 5)))
      const pathField = String(form.get("path") || "").trim()
      const file = form.get("file")

      if (pathField) {
        const abs = path.isAbsolute(pathField)
          ? pathField
          : path.resolve(repoRoot(), pathField)
        if (!fs.existsSync(abs)) {
          return NextResponse.json({ error: `path not found: ${abs}` }, { status: 400 })
        }
        videoPath = abs
      } else if (file && file instanceof File) {
        const uploadPath = path.join(out, "input" + path.extname(file.name || ".mp4"))
        const buf = Buffer.from(await file.arrayBuffer())
        fs.writeFileSync(uploadPath, buf)
        videoPath = uploadPath
      } else {
        return NextResponse.json(
          { error: "Provide a video file upload or a server path" },
          { status: 400 }
        )
      }
    } else {
      const body = (await req.json()) as { path?: string; n?: number; runId?: string }
      if (body.runId) {
        const existing = readRun(body.runId)
        if (!existing) {
          return NextResponse.json({ error: "run not found" }, { status: 404 })
        }
        return NextResponse.json(existing)
      }
      n = Math.max(3, Math.min(5, Number(body.n || 5)))
      if (!body.path) {
        return NextResponse.json({ error: "path required" }, { status: 400 })
      }
      const abs = path.isAbsolute(body.path)
        ? body.path
        : path.resolve(repoRoot(), body.path)
      if (!fs.existsSync(abs)) {
        return NextResponse.json({ error: `path not found: ${abs}` }, { status: 400 })
      }
      videoPath = abs
    }

    // Keep a pointer to the source video for clip extraction.
    fs.writeFileSync(path.join(out, "source.txt"), videoPath)

    const { elapsed_s } = await runSuggest(videoPath, out, n)
    const metaPath = path.join(out, "moments.json")
    if (!fs.existsSync(metaPath)) {
      return NextResponse.json({ error: "suggest produced no moments.json" }, { status: 500 })
    }
    const raw = JSON.parse(fs.readFileSync(metaPath, "utf8")) as Omit<SuggestRun, "runId">
    // Rewrite frame paths to be relative inside the run dir for serving.
    const moments = raw.moments.map((m, i) => {
      const name = `moment_${String(i + 1).padStart(2, "0")}.jpg`
      const localFrame = path.join(out, "frames", name)
      return { ...m, frame: localFrame }
    })
    const run: SuggestRun = withUrls({
      ...raw,
      moments,
      runId,
      elapsed_s,
      video: videoPath,
    })
    const persisted = {
      ...run,
      moments: run.moments.map((m) => {
        const { frameUrl: _fu, clipUrl: _cu, ...rest } = m
        void _fu
        void _cu
        return rest
      }),
    }
    fs.writeFileSync(metaPath, JSON.stringify(persisted, null, 2) + "\n")
    return NextResponse.json(run)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
