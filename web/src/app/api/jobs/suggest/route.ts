import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import {
  newRunId,
  repoRoot,
  runDir,
  spawnJob,
  writeJob,
  type JobStatus,
} from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 60

export async function POST(req: NextRequest) {
  const jobId = newRunId()
  const out = runDir(jobId)
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
      const body = (await req.json()) as { path?: string; n?: number }
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

    fs.writeFileSync(path.join(out, "source.txt"), videoPath + "\n")

    const status: JobStatus = {
      id: jobId,
      type: "suggest",
      status: "queued",
      progress: "Queued",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    writeJob(jobId, status)

    spawnJob([
      "-m",
      "product_placement.jobs",
      "suggest",
      "--job-id",
      jobId,
      "--video",
      videoPath,
      "-n",
      String(n),
    ])

    return NextResponse.json({ jobId, ...status })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
