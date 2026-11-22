import { spawn } from "child_process"
import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { runDir } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ runId: string }> }
) {
  const { runId } = await ctx.params
  const start = Number(req.nextUrl.searchParams.get("start") || 0)
  const end = Number(req.nextUrl.searchParams.get("end") || start + 5)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return NextResponse.json({ error: "bad start/end" }, { status: 400 })
  }

  const dir = runDir(runId)
  const sourcePath = path.join(dir, "source.txt")
  if (!fs.existsSync(sourcePath)) {
    return NextResponse.json({ error: "source video missing for run" }, { status: 404 })
  }
  const video = fs.readFileSync(sourcePath, "utf8").trim()
  if (!fs.existsSync(video)) {
    return NextResponse.json({ error: `source not found: ${video}` }, { status: 404 })
  }

  const clipsDir = path.join(dir, "clips")
  fs.mkdirSync(clipsDir, { recursive: true })
  const out = path.join(clipsDir, `${start.toFixed(2)}_${end.toFixed(2)}.mp4`)
  const duration = Math.max(0.1, end - start)

  if (!fs.existsSync(out)) {
    await new Promise<void>((resolve, reject) => {
      const child = spawn(
        "ffmpeg",
        [
          "-y",
          "-ss",
          String(start),
          "-i",
          video,
          "-t",
          String(duration),
          "-c:v",
          "libx264",
          "-pix_fmt",
          "yuv420p",
          "-an",
          "-movflags",
          "+faststart",
          out,
        ],
        { stdio: ["ignore", "ignore", "pipe"] }
      )
      let err = ""
      child.stderr.on("data", (c) => {
        err += c.toString()
      })
      child.on("close", (code) => {
        if (code === 0) resolve()
        else reject(new Error(err.slice(-500) || `ffmpeg ${code}`))
      })
    }).catch((e) => {
      throw e
    })
  }

  const buf = fs.readFileSync(out)
  return new NextResponse(buf, {
    headers: {
      "Content-Type": "video/mp4",
      "Cache-Control": "public, max-age=3600",
      "Content-Length": String(buf.length),
    },
  })
}
