import { NextResponse } from "next/server"

import { ensureCatalogDb, wakeMoviesImport } from "@/lib/catalog"

export const runtime = "nodejs"
export const maxDuration = 30

export async function POST() {
  try {
    ensureCatalogDb()
    const woke = wakeMoviesImport()
    return NextResponse.json(
      {
        accepted: true,
        status: "queued",
        ...woke,
      },
      { status: 202 }
    )
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
