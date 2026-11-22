import fs from "fs"
import path from "path"

import { NextRequest, NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb } from "@/lib/catalog"
import { dataRoot } from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 60

const EPISODE_ID = /^[0-9a-f]{12}$/
const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{3}$/

export async function GET(req: NextRequest) {
  try {
    ensureCatalogDb()
    const episodeId = req.nextUrl.searchParams.get("episodeId") || ""
    const brandId = req.nextUrl.searchParams.get("brandId") || ""
    if (!episodeId) {
      return NextResponse.json({ error: "episodeId required" }, { status: 400 })
    }
    const args = ["list-moments", "--episode-id", episodeId]
    if (brandId) args.push("--brand-id", brandId)
    const moments = catalogCmd(args)
    return NextResponse.json({ moments })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  try {
    ensureCatalogDb()
    const contentType = req.headers.get("content-type") || ""
    if (contentType.includes("multipart/form-data")) {
      const form = await req.formData()
      const episodeId = String(form.get("episodeId") || "").trim()
      const momentsRaw = String(form.get("moments") || "").trim()
      if (!EPISODE_ID.test(episodeId)) {
        return NextResponse.json({ error: "invalid episode id" }, { status: 400 })
      }
      if (!momentsRaw) {
        return NextResponse.json({ error: "moments required" }, { status: 400 })
      }
      const staging = path.join(dataRoot(), "_upload", `adopt_${Date.now()}`)
      const framesDir = path.join(staging, "frames")
      fs.mkdirSync(framesDir, { recursive: true })
      const momentsPath = path.join(staging, "moments.json")
      fs.writeFileSync(momentsPath, momentsRaw)
      for (const [key, value] of form.entries()) {
        if (!(value instanceof File)) continue
        if (!/^moment_\d{2}\.jpg$/.test(value.name || key)) continue
        const name = value.name || key
        fs.writeFileSync(path.join(framesDir, name), Buffer.from(await value.arrayBuffer()))
      }
      const saved = catalogCmd([
        "replace-moments",
        "--episode-id",
        episodeId,
        "--moments-json",
        momentsPath,
        "--frames-dir",
        framesDir,
      ])
      return NextResponse.json(saved)
    }

    const body = (await req.json()) as { episodeId?: string; jobId?: string }
    const episodeId = String(body.episodeId || "").trim()
    const jobId = String(body.jobId || "").trim()
    if (!EPISODE_ID.test(episodeId) || !JOB_ID.test(jobId)) {
      return NextResponse.json({ error: "episodeId and jobId required" }, { status: 400 })
    }
    const saved = catalogCmd([
      "adopt-suggest-job",
      "--episode-id",
      episodeId,
      "--job-id",
      jobId,
    ])
    return NextResponse.json(saved)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    const status = /not found|invalid/i.test(message) ? 400 : 500
    return NextResponse.json({ error: message }, { status })
  }
}
