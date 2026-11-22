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
      skipped?: boolean
    }
    if (!result.skipped) {
      for (const ep of result.episodes || []) {
        if (!ep.suggest_status || ep.suggest_status === "queued") {
          spawnEpisodeSuggest(ep.id)
        }
      }
    }
    return NextResponse.json(result)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
