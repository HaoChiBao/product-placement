import { NextRequest, NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb } from "@/lib/catalog"

export const runtime = "nodejs"
export const maxDuration = 60

export async function GET(req: NextRequest) {
  try {
    ensureCatalogDb()
    const all = req.nextUrl.searchParams.get("all")
    const watchable = req.nextUrl.searchParams.get("watchable")
    const args = ["list-titles"]
    // Default: only titles with a ready/playable episode. Opt out with ?all=1.
    const wantAll = all === "1" || all === "true"
    const wantWatchable =
      !wantAll && (watchable === null || watchable === "1" || watchable === "true")
    if (wantWatchable) {
      args.push("--watchable")
    }
    const titles = catalogCmd(args)
    return NextResponse.json({ titles })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    ensureCatalogDb()
    const body = (await req.json()) as {
      name?: string
      kind?: string
      synopsis?: string
      attribution?: string
    }
    if (!body.name?.trim()) {
      return NextResponse.json({ error: "name required" }, { status: 400 })
    }
    const title = catalogCmd([
      "create-title",
      "--name",
      body.name.trim(),
      "--kind",
      body.kind || "movie",
      "--synopsis",
      body.synopsis || "",
      "--attribution",
      body.attribution || "",
    ])
    return NextResponse.json(title)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
