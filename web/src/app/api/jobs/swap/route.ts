import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import {
  MAX_SWAP_INPUT_S,
  estimateSwapCostUsd,
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

    if (!confirm) {
      return NextResponse.json(
        { error: "confirm=true is required — swaps cost ~$1.63 per 5s at 720p" },
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
    if (duration > MAX_SWAP_INPUT_S + 1e-6) {
      return NextResponse.json(
        { error: `moment window ${duration.toFixed(2)}s exceeds max ${MAX_SWAP_INPUT_S}s` },
        { status: 400 }
      )
    }

    let productPath = ""
    if (productPathField) {
      const abs = path.isAbsolute(productPathField)
        ? productPathField
        : path.resolve(repoRoot(), productPathField)
      if (!fs.existsSync(abs)) {
        return NextResponse.json({ error: `product not found: ${abs}` }, { status: 400 })
      }
      productPath = abs
    } else if (productFile && productFile instanceof File) {
      const uploadPath = path.join(
        out,
        "product" + path.extname(productFile.name || ".png")
      )
      fs.writeFileSync(uploadPath, Buffer.from(await productFile.arrayBuffer()))
      productPath = uploadPath
    } else {
      return NextResponse.json(
        { error: "Provide a product image upload or productPath" },
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
    const estimated = estimateSwapCostUsd(holdS)
    const swapTarget = target || moment.whats_there || "object"

    const status: JobStatus = {
      id: jobId,
      type: "swap",
      status: "queued",
      progress: "Queued",
      estimated_cost_usd: estimated,
      mock: ["1", "true", "yes", "on"].includes(
        (process.env.MOCK_HIGGSFIELD || "").trim().toLowerCase()
      ),
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    writeJob(jobId, status)

    spawnJob(
      [
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
      ],
      { jobId }
    )

    return NextResponse.json({ jobId, estimated_cost_usd: estimated, ...status })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
