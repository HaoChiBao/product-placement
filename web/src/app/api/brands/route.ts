import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"
import { spawnSync } from "child_process"

import { dataRoot, pythonBin, pythonEnv, repoRoot } from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 60

function brandsDir(): string {
  const dir = path.join(dataRoot(), "brands")
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

export async function GET() {
  try {
    const root = brandsDir()
    const brands = []
    for (const name of fs.readdirSync(root)) {
      if (name.startsWith("_")) continue
      const meta = path.join(root, name, "brand.json")
      if (fs.existsSync(meta)) {
        brands.push(JSON.parse(fs.readFileSync(meta, "utf8")))
      }
    }
    brands.sort((a, b) => String(a.name).localeCompare(String(b.name)))
    return NextResponse.json({ brands })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    const form = await req.formData()
    const name = String(form.get("name") || "").trim()
    const brief = String(form.get("brief") || "").trim()
    const preferRaw = String(form.get("prefer") || "")
    const avoidRaw = String(form.get("avoid") || "")
    const placement = String(form.get("placement") || "resting")
    const minArea = Number(form.get("min_area") || 0.02)
    if (!name) {
      return NextResponse.json({ error: "name required" }, { status: 400 })
    }

    const prefer = preferRaw
      ? preferRaw.split("|").map((s) => s.trim()).filter(Boolean)
      : []
    const avoid = avoidRaw
      ? avoidRaw.split("|").map((s) => s.trim()).filter(Boolean)
      : []

    const staging = path.join(brandsDir(), "_upload_" + Date.now())
    fs.mkdirSync(staging, { recursive: true })
    const assetNames: string[] = []
    for (const file of form.getAll("assets")) {
      if (!(file instanceof File)) continue
      const safe = path.basename(file.name || "asset.png").replace(/[^\w.\-]+/g, "_")
      fs.writeFileSync(path.join(staging, safe), Buffer.from(await file.arrayBuffer()))
      assetNames.push(safe)
    }

    const payloadPath = path.join(staging, "payload.json")
    fs.writeFileSync(
      payloadPath,
      JSON.stringify({
        name,
        brief,
        prefer,
        avoid,
        placement,
        min_area: minArea,
        assets: assetNames,
      })
    )

    const script = `
import json, shutil, sys
from pathlib import Path
from product_placement.brand import Brand, BrandRules, brand_dir, new_brand_id, parse_brief, save_brand

staging = Path(sys.argv[1])
payload = json.loads((staging / "payload.json").read_text())
bid = new_brand_id()
rules = BrandRules(
    prefer=list(payload.get("prefer") or []),
    avoid=list(payload.get("avoid") or []),
    placement=str(payload.get("placement") or "resting"),
    min_area=float(payload.get("min_area") or 0.02),
)
if not rules.prefer and not rules.avoid and payload.get("brief"):
    rules = parse_brief(payload["brief"])
brand = Brand(id=bid, name=payload["name"], brief=payload.get("brief") or "", assets=[], rules=rules)
save_brand(brand)
dest = brand_dir(bid)
assets = []
for name in payload.get("assets") or []:
    src = staging / name
    if src.is_file():
        shutil.copy2(src, dest / name)
        assets.append(name)
brand.assets = assets
save_brand(brand)
shutil.rmtree(staging, ignore_errors=True)
print(json.dumps(brand.to_dict()))
`
    const res = spawnSync(pythonBin(), ["-c", script, staging], {
      cwd: repoRoot(),
      env: pythonEnv(),
      encoding: "utf8",
    })
    if (res.status !== 0) {
      return NextResponse.json(
        { error: (res.stderr || res.stdout || "create brand failed").trim() },
        { status: 500 }
      )
    }
    return NextResponse.json(JSON.parse(res.stdout))
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
