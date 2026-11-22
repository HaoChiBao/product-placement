import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import {
  catalogCmd,
  ensureCatalogDb,
  spawnEpisodeSuggest,
} from "@/lib/catalog"
import { dataRoot } from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 120

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ id: string }> }
) {
  try {
    ensureCatalogDb()
    const { id } = await ctx.params
    const episodes = catalogCmd(["list-episodes", "--title-id", id])
    return NextResponse.json({ episodes })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> }
) {
  try {
    ensureCatalogDb()
    const { id } = await ctx.params
    const form = await req.formData()
    const name = String(form.get("name") || "").trim()
    const season = Number(form.get("season") || 1)
    const episode = Number(form.get("episode") || 1)
    const file = form.get("video")
    if (!name) {
      return NextResponse.json({ error: "name required" }, { status: 400 })
    }
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "video file required" }, { status: 400 })
    }

    const staging = path.join(dataRoot(), "_upload", `ep_${Date.now()}`)
    fs.mkdirSync(staging, { recursive: true })
    const ext = path.extname(file.name || ".mp4") || ".mp4"
    const videoPath = path.join(staging, `video${ext}`)
    fs.writeFileSync(videoPath, Buffer.from(await file.arrayBuffer()))

    const created = catalogCmd([
      "create-episode",
      "--title-id",
      id,
      "--name",
      name,
      "--season",
      String(season),
      "--episode",
      String(episode),
      "--video-path",
      videoPath,
    ]) as { id: string }

    spawnEpisodeSuggest(created.id)
    try {
      fs.rmSync(staging, { recursive: true, force: true })
    } catch {
      /* ignore */
    }
    return NextResponse.json(created)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
