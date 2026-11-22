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
    if (brandId) {
      // The brand's own moments once its scan is ready, plus that scan's state.
      const payload = catalogCmd([
        "brand-moments",
        "--episode-id",
        episodeId,
        "--brand-id",
        brandId,
      ])
      return NextResponse.json(payload)
    }
    const moments = catalogCmd(["list-moments", "--episode-id", episodeId])
    return NextResponse.json({ moments })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
