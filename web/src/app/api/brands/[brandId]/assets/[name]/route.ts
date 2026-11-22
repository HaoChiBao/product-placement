import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { isSafeBrandId, safeAssetName } from "@/lib/brand-id"
import { dataRoot } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ brandId: string; name: string }> }
) {
  const { brandId, name } = await ctx.params
  if (!isSafeBrandId(brandId)) {
    return NextResponse.json({ error: "invalid brand id" }, { status: 400 })
  }
  const safe = safeAssetName(name)
  if (!safe) {
    return NextResponse.json({ error: "invalid asset name" }, { status: 400 })
  }
  const brandRoot = path.resolve(dataRoot(), "brands", brandId)
  const filePath = path.resolve(brandRoot, safe)
  if (!filePath.startsWith(brandRoot + path.sep)) {
    return NextResponse.json({ error: "invalid asset name" }, { status: 400 })
  }
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
