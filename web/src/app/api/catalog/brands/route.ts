import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb } from "@/lib/catalog"
import { dataRoot } from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 60

export async function GET() {
  try {
    ensureCatalogDb()
    const brands = catalogCmd(["list-brands"])
    return NextResponse.json({ brands })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    ensureCatalogDb()
    const form = await req.formData()
    const name = String(form.get("name") || "").trim()
    const brief = String(form.get("brief") || "").trim()
    if (!name) {
      return NextResponse.json({ error: "name required" }, { status: 400 })
    }

    const args = ["create-brand", "--name", name, "--brief", brief]
    const asset = form.get("asset")
    const staging = path.join(dataRoot(), "_upload", `brand_${Date.now()}`)
    if (asset instanceof File) {
      fs.mkdirSync(staging, { recursive: true })
      const safe = path.basename(asset.name || "asset.png").replace(/[^\w.\-]+/g, "_")
      const assetPath = path.join(staging, safe)
      fs.writeFileSync(assetPath, Buffer.from(await asset.arrayBuffer()))
      args.push("--asset", assetPath)
    }

    const brand = catalogCmd(args)
    try {
      fs.rmSync(staging, { recursive: true, force: true })
    } catch {
      /* ignore */
    }
    return NextResponse.json(brand)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
