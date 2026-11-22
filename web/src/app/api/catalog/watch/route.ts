import { NextRequest, NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb } from "@/lib/catalog"

export const runtime = "nodejs"

export async function GET(req: NextRequest) {
  try {
    ensureCatalogDb()
    const slug = req.nextUrl.searchParams.get("slug") || ""
    const season = req.nextUrl.searchParams.get("season") || "1"
    const episode = req.nextUrl.searchParams.get("episode") || "1"
    const brandId = req.nextUrl.searchParams.get("brandId") || ""
    if (!slug) {
      return NextResponse.json({ error: "slug required" }, { status: 400 })
    }
    const args = [
      "get-watch-payload",
      "--title-slug",
      slug,
      "--season",
      season,
      "--episode",
      episode,
    ]
    if (brandId) args.push("--brand-id", brandId)
    const payload = catalogCmd(args)
    return NextResponse.json(payload)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    const lower = message.toLowerCase()
    if (
      lower.includes("episode not found") ||
      lower.includes("title not found") ||
      lower.includes('"error": "not found"')
    ) {
      return NextResponse.json({ error: message }, { status: 404 })
    }
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
