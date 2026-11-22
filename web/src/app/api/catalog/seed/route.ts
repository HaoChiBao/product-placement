import { NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb, spawnEpisodeSuggest } from "@/lib/catalog"

export const runtime = "nodejs"
export const maxDuration = 300

export async function POST() {
  try {
    ensureCatalogDb()
    const result = catalogCmd(["seed-blender"]) as {
      titles?: unknown
      episodes?: { id: string; suggest_status?: string }[]
      suggest_queue?: { id: string; suggest_status?: string }[]
      skipped?: boolean
    }
    // Drain suggest queue. Jobs atomically claim the row so a concurrent
    // boot drain or double-click is a no-op instead of a double suggest().
    const queue = result.suggest_queue?.length
      ? result.suggest_queue
      : result.episodes || []
    for (const ep of queue) {
      const st = (ep.suggest_status || "queued").toLowerCase()
      if (st === "queued" || st === "failed" || !ep.suggest_status) {
        spawnEpisodeSuggest(ep.id)
      }
    }
    return NextResponse.json(result)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
