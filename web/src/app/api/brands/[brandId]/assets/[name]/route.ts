import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { dataRoot } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ brandId: string; name: string }> }
) {
  const { brandId, name } = await ctx.params
  const safe = path.basename(name)
  const filePath = path.join(dataRoot(), "brands", brandId, safe)
  if (!fs.existsSync(filePath)) {
    return NextResponse.json({ error: "not found" }, { status: 404 })
  }
  const buf = fs.readFileSync(filePath)
  const ext = path.extname(safe).toLowerCase()
  const type =
    ext === ".png"
      ? "image/png"
      : ext === ".jpg" || ext === ".jpeg"
        ? "image/jpeg"
        : ext === ".webp"
          ? "image/webp"
          : "application/octet-stream"
  return new NextResponse(buf, {
    headers: {
      "Content-Type": type,
      "Cache-Control": "private, max-age=120",
    },
  })
}
