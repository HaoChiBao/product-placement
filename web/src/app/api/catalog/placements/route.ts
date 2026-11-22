import { NextRequest, NextResponse } from "next/server"

import {
  catalogCmd,
  ensureCatalogDb,
  spawnPlacementSwap,
} from "@/lib/catalog"

export const runtime = "nodejs"
export const maxDuration = 60

export async function GET(req: NextRequest) {
  try {
    ensureCatalogDb()
    const brandId = req.nextUrl.searchParams.get("brandId") || ""
    const episodeId = req.nextUrl.searchParams.get("episodeId") || ""
    if (!brandId) {
      return NextResponse.json({ error: "brandId required" }, { status: 400 })
    }
    const args = ["list-placements", "--brand-id", brandId]
    if (episodeId) args.push("--episode-id", episodeId)
    const placements = catalogCmd(args)
    return NextResponse.json({ placements })
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
      momentId?: string
      status?: string
      testMode?: boolean
      confirm?: boolean
    }
    if (!body.brandId || !body.momentId || !body.status) {
      return NextResponse.json(
        { error: "brandId, momentId, status required" },
        { status: 400 }
      )
    }
    const testMode = body.testMode !== false
    if (body.status === "approved" && !testMode && !body.confirm) {
      return NextResponse.json(
        {
          error:
            "Paid placement requires confirm=true and testMode=false. Leave testMode=true for mock swaps.",
        },
        { status: 400 }
      )
    }

    const placement = catalogCmd([
      "upsert-placement",
      "--brand-id",
      body.brandId,
      "--moment-id",
      body.momentId,
      "--status",
      body.status,
    ]) as { id: string; status: string; spawn?: boolean }

    // Only spawn when upsert newly transitioned into queued (not retries/double-clicks).
    if (placement.spawn) {
      spawnPlacementSwap(placement.id, {
        testMode,
        confirm: true,
      })
    }

    return NextResponse.json(placement)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
