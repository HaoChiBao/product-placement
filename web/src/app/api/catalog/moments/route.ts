import { NextRequest, NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb } from "@/lib/catalog"

export const runtime = "nodejs"

export async function GET(req: NextRequest) {
  try {
    ensureCatalogDb()
    const episodeId = req.nextUrl.searchParams.get("episodeId") || ""
    const brandId = req.nextUrl.searchParams.get("brandId") || ""
    if (!episodeId) {
      return NextResponse.json({ error: "episodeId required" }, { status: 400 })
    }
    const args = ["list-moments", "--episode-id", episodeId]
    if (brandId) args.push("--brand-id", brandId)
    const moments = catalogCmd(args)
    return NextResponse.json({ moments })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
