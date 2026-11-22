import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"
import { spawnSync } from "child_process"

import { isSafeBrandId } from "@/lib/brand-id"
import { dataRoot, pythonBin, pythonEnv, repoRoot } from "@/lib/runs"

export const runtime = "nodejs"

function brandMeta(brandId: string): string {
  return path.join(dataRoot(), "brands", brandId, "brand.json")
}

function rejectUnsafeId(brandId: string): NextResponse | null {
  if (!isSafeBrandId(brandId)) {
    return NextResponse.json({ error: "invalid brand id" }, { status: 400 })
  }
  return null
}

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ brandId: string }> }
) {
  const { brandId } = await ctx.params
  const bad = rejectUnsafeId(brandId)
  if (bad) return bad
  const meta = brandMeta(brandId)
  if (!fs.existsSync(meta)) {
    return NextResponse.json({ error: "brand not found" }, { status: 404 })
  }
  const brand = JSON.parse(fs.readFileSync(meta, "utf8"))
  brand.asset_urls = (brand.assets || []).map(
    (name: string) => `/api/brands/${brandId}/assets/${encodeURIComponent(name)}`
  )
  return NextResponse.json(brand)
}

export async function PATCH(
  req: NextRequest,
  ctx: { params: Promise<{ brandId: string }> }
) {
  const { brandId } = await ctx.params
  const bad = rejectUnsafeId(brandId)
  if (bad) return bad
  const meta = brandMeta(brandId)
  if (!fs.existsSync(meta)) {
    return NextResponse.json({ error: "brand not found" }, { status: 404 })
  }
  const body = await req.json()
  const script = `
import json, sys
from product_placement.brand import BrandRules, load_brand, save_brand, parse_brief

brand_id = sys.argv[1]
body = json.loads(sys.argv[2])
brand = load_brand(brand_id)
if "name" in body: brand.name = str(body["name"])
if "brief" in body: brand.brief = str(body["brief"])
if "rules" in body:
    brand.rules = BrandRules.from_dict(body["rules"])
elif body.get("reparse_brief"):
    brand.rules = parse_brief(brand.brief)
save_brand(brand)
print(json.dumps(brand.to_dict()))
`
  const res = spawnSync(
    pythonBin(),
    ["-c", script, brandId, JSON.stringify(body)],
    { cwd: repoRoot(), env: pythonEnv(), encoding: "utf8" }
  )
  if (res.status !== 0) {
    return NextResponse.json(
      { error: (res.stderr || res.stdout || "update failed").trim() },
      { status: 500 }
    )
  }
  return NextResponse.json(JSON.parse(res.stdout))
}

export async function DELETE(
  _req: NextRequest,
  ctx: { params: Promise<{ brandId: string }> }
) {
  const { brandId } = await ctx.params
  const bad = rejectUnsafeId(brandId)
  if (bad) return bad
  const root = path.resolve(dataRoot(), "brands")
  const dir = path.resolve(root, brandId)
  if (!dir.startsWith(root + path.sep)) {
    return NextResponse.json({ error: "invalid brand id" }, { status: 400 })
  }
  if (!fs.existsSync(dir)) {
    return NextResponse.json({ error: "brand not found" }, { status: 404 })
  }
  fs.rmSync(dir, { recursive: true, force: true })
  return NextResponse.json({ ok: true })
}
