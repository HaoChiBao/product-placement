import { NextRequest, NextResponse } from "next/server"

import {
  catalogCmd,
  ensureCatalogDb,
  spawnPlacementSwap,
} from "@/lib/catalog"
import { planGeneration, resolveSwapMethod } from "@/lib/generation"

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
      method?: string
      /** false: record the yes/no call only; approving queues no swap. */
      generate?: boolean
    }
    if (!body.brandId || !body.momentId || !body.status) {
      return NextResponse.json(
        { error: "brandId, momentId, status required" },
        { status: 400 }
      )
    }

    const generate = body.generate !== false
    const plan = planGeneration({
      testMode: body.testMode,
      method: resolveSwapMethod(body.method),
    })
    if (generate && body.status === "approved" && !plan.testMode && !body.confirm) {
      return NextResponse.json(
        {
          error:
            "Paid placement requires confirm=true. Pass testMode=true only for intentional mock swaps.",
        },
        { status: 400 }
      )
    }

    let placement = catalogCmd([
      "upsert-placement",
      "--brand-id",
      body.brandId,
      "--moment-id",
      body.momentId,
      "--status",
      body.status,
      ...(generate ? [] : ["--no-generate"]),
    ]) as {
      id: string
      status: string
      spawn?: boolean
      episode_id?: string
      error?: string | null
    }

    if (placement.spawn) {
      if (!plan.canSpawn) {
        placement = catalogCmd([
          "update-placement",
          "--id",
          placement.id,
          "--error",
          plan.holdError || "Generation queued: key missing",
          "--test-mode",
          "0",
        ]) as typeof placement
        placement.spawn = false
      } else {
        spawnPlacementSwap(placement.id, {
          testMode: plan.testMode,
          confirm: true,
          method: plan.method,
        })
      }
    }

    return NextResponse.json(placement)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
