import { NextRequest, NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb } from "@/lib/catalog"

export const runtime = "nodejs"
export const maxDuration = 60

export async function GET(req: NextRequest) {
  try {
    ensureCatalogDb()
    const brandId = req.nextUrl.searchParams.get("brandId") || ""
    if (!brandId) {
      return NextResponse.json({ error: "brandId required" }, { status: 400 })
    }
    const campaigns = catalogCmd(["list-campaigns", "--brand-id", brandId])
    return NextResponse.json({ campaigns })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    ensureCatalogDb()
    const body = (await req.json()) as {
      brandId?: string
      episodeId?: string
      titleId?: string
      status?: string
    }
    if (!body.brandId || !body.episodeId) {
      return NextResponse.json(
        { error: "brandId and episodeId required" },
        { status: 400 }
      )
    }
    const args = [
      "upsert-campaign",
      "--brand-id",
      body.brandId,
      "--episode-id",
      body.episodeId,
    ]
    if (body.titleId) args.push("--title-id", body.titleId)
    if (body.status) args.push("--status", body.status)
    const campaign = catalogCmd(args)
    return NextResponse.json(campaign)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
