import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { runDir } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ runId: string; name: string }> }
) {
  const { runId, name } = await ctx.params
  const safe = path.basename(name)
  const filePath = path.join(runDir(runId), "frames", safe)
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ error: "frame not found" }, { status: 404 })
  }
  const buf = fs.readFileSync(filePath)
  return new NextResponse(buf, {
    headers: {
      "Content-Type": "image/jpeg",
      "Cache-Control": "public, max-age=3600",
    },
  })
}
