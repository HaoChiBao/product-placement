import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { jobFileType } from "@/lib/job-files"
import { runDir } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ jobId: string; name: string }> }
) {
  const { jobId, name } = await ctx.params
  const safe = path.basename(name)
  const type = jobFileType(safe)
  if (!type) {
    return NextResponse.json({ error: "file not allowed" }, { status: 400 })
  }
  const filePath = path.join(runDir(jobId), safe)
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ error: "not found" }, { status: 404 })
  }
  const buf = fs.readFileSync(filePath)
  const download = req.nextUrl.searchParams.get("download") === "1"
  const headers: Record<string, string> = {
    "Content-Type": type,
    "Content-Length": String(buf.length),
    "Cache-Control": "private, max-age=60",
  }
  if (download) {
    headers["Content-Disposition"] = `attachment; filename="${safe}"`
  }
  return new NextResponse(buf, { headers })
}
