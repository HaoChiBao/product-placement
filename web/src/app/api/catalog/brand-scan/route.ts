import { NextRequest, NextResponse } from "next/server"

import { isSafeBrandId } from "@/lib/brand-id"
import { catalogCmd, ensureCatalogDb, spawnBrandScan } from "@/lib/catalog"

export const runtime = "nodejs"
export const maxDuration = 60

type BrandScan = { status?: string; updated_at?: string } | null

/** A running scan nobody has touched for this long is treated as dead and restarted. */
const STALE_MS = 15 * 60 * 1000

/**
 * Start the brand-aware moment finder for one episode (POST {episodeId, brandId}).
 * Idempotent: does nothing while a scan runs or once it's ready. The job itself
 * claims the scan atomically, so a double click can't run it twice.
 */
export async function POST(req: NextRequest) {
  try {
    ensureCatalogDb()
    const body = (await req.json()) as { episodeId?: string; brandId?: string }
    const episodeId = String(body.episodeId || "").trim()
    const brandId = String(body.brandId || "").trim()
    if (!isSafeBrandId(episodeId) || !isSafeBrandId(brandId)) {
      return NextResponse.json({ error: "episodeId and brandId required" }, { status: 400 })
    }

    const scan = catalogCmd([
      "get-brand-scan",
      "--episode-id",
      episodeId,
      "--brand-id",
      brandId,
    ]) as BrandScan
    const status = scan?.status || ""
    const age = scan?.updated_at ? Date.now() - new Date(scan.updated_at).getTime() : Infinity
    const busy = status === "ready" || (status === "running" && age < STALE_MS)
    if (!busy) spawnBrandScan(episodeId, brandId)

    return NextResponse.json({
      started: !busy,
      scan: busy ? scan : { ...(scan || {}), episode_id: episodeId, brand_id: brandId, status: "running" },
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
