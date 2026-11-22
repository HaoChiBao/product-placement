import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { runDir } from "@/lib/runs"

export const runtime = "nodejs"

const ALLOWED = new Set(["original.mp4", "result.mp4", "contact_sheet.jpg"])

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ jobId: string; name: string }> }
) {
  const { jobId, name } = await ctx.params
  const safe = path.basename(name)
  if (!ALLOWED.has(safe)) {
    return NextResponse.json({ error: "file not allowed" }, { status: 400 })
  }
  const filePath = path.join(runDir(jobId), safe)
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ error: "not found" }, { status: 404 })
  }
  const buf = fs.readFileSync(filePath)
  const download = req.nextUrl.searchParams.get("download") === "1"
  const type = safe.endsWith(".jpg") ? "image/jpeg" : "video/mp4"
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
