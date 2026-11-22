import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { resolveSwapMethod } from "@/lib/generation"
import {
  estimateSwapCostUsd,
  estimateWanSwapCostUsd,
  newRunId,
  readRun,
  repoRoot,
  runDir,
  spawnJob,
  writeJob,
  type JobStatus,
  type Region,
} from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 60

export async function POST(req: NextRequest) {
  const jobId = newRunId()
  const out = runDir(jobId)
  fs.mkdirSync(path.join(out, "work"), { recursive: true })

  try {
    const form = await req.formData()
    const suggestRunId = String(form.get("runId") || "").trim()
    const momentIndex = Number(form.get("momentIndex") ?? 0)
    const target = String(form.get("target") || "").trim()
    const confirm = String(form.get("confirm") || "") === "true"
    const productPathField = String(form.get("productPath") || "").trim()
    const productFile = form.get("product")
    const brandId = String(form.get("brandId") || "").trim()
    const method = resolveSwapMethod(String(form.get("method") || ""))
    const mock = ["1", "true", "yes", "on"].includes(
      (process.env.MOCK_HIGGSFIELD || "").trim().toLowerCase()
    )

    if (!confirm) {
      const price =
        method === "wan" && !mock
          ? "Wan 3.0 swaps cost about $1.20 per 10s at 720p"
          : "swaps cost ~$1.63 per 5s at 720p"
      return NextResponse.json(
        { error: `confirm=true is required — ${price}` },
        { status: 400 }
      )
    }
    if (!suggestRunId) {
      return NextResponse.json({ error: "runId required" }, { status: 400 })
    }

    const run = readRun(suggestRunId)
    if (!run) {
      return NextResponse.json({ error: "suggest run not found" }, { status: 404 })
    }
    const moment = run.moments[momentIndex]
    if (!moment) {
      return NextResponse.json({ error: "momentIndex out of range" }, { status: 400 })
    }

    const duration = moment.end_s - moment.start_s

    let productPath = ""
    if (brandId) {
      const { isSafeBrandId, safeAssetName } = await import("@/lib/brand-id")
      const { dataRoot } = await import("@/lib/runs")
      if (isSafeBrandId(brandId)) {
        const brandRoot = path.resolve(dataRoot(), "brands", brandId)
        const brandMeta = path.join(brandRoot, "brand.json")
        if (fs.existsSync(brandMeta)) {
          const brand = JSON.parse(fs.readFileSync(brandMeta, "utf8")) as {
            assets?: string[]
          }
          const asset = safeAssetName((brand.assets || [])[0] || "")
          if (asset) {
            const candidate = path.resolve(brandRoot, asset)
            if (
              candidate.startsWith(brandRoot + path.sep) &&
              fs.existsSync(candidate)
            ) {
              productPath = candidate
            }
          }
        }
      }
    }
    if (!productPath && productPathField) {
      const abs = path.isAbsolute(productPathField)
        ? productPathField
        : path.resolve(repoRoot(), productPathField)
      if (!fs.existsSync(abs)) {
        return NextResponse.json({ error: `product not found: ${abs}` }, { status: 400 })
      }
      productPath = abs
    } else if (!productPath && productFile && productFile instanceof File) {
      const uploadPath = path.join(
        out,
        "product" + path.extname(productFile.name || ".png")
      )
      fs.writeFileSync(uploadPath, Buffer.from(await productFile.arrayBuffer()))
      productPath = uploadPath
    }
    if (!productPath || !fs.existsSync(productPath)) {
      return NextResponse.json(
        { error: "Provide a product image, productPath, or brandId with assets" },
        { status: 400 }
      )
    }

    const sourcePath = path.join(runDir(suggestRunId), "source.txt")
    const video = fs.existsSync(sourcePath)
      ? fs.readFileSync(sourcePath, "utf8").trim()
      : run.video
    if (!video || !fs.existsSync(video)) {
      return NextResponse.json({ error: "source video missing" }, { status: 400 })
    }

    const region: Region = moment.region
    const holdS = Math.min(4.96, Math.max(duration, 2))
    const estimated =
      method === "wan" && !mock ? estimateWanSwapCostUsd(duration) : estimateSwapCostUsd(holdS)
    const swapTarget = target || moment.whats_there || "object"

    const status: JobStatus = {
      id: jobId,
      type: "swap",
      status: "queued",
      progress: "Queued",
      estimated_cost_usd: estimated,
      mock,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    writeJob(jobId, status)

    const args = [
      "-m",
      "product_placement.jobs",
      "swap",
      "--job-id",
      jobId,
      "--video",
      video,
      "--product",
      productPath,
      "--start",
      String(moment.start_s),
      "--end",
      String(moment.end_s),
      "--region-json",
      JSON.stringify(region),
      "--target",
      swapTarget,
      "--confirm",
      "--resolution",
      "720p",
      "--method",
      method,
    ]
    if (brandId) args.push("--brand-id", brandId)
    spawnJob(args, { jobId })

    return NextResponse.json({ jobId, estimated_cost_usd: estimated, ...status })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
