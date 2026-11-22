import { NextRequest, NextResponse } from "next/server"

import type { ResearchJob } from "@/lib/research"
import { newRunId, spawnJob, writeJob } from "@/lib/runs"

export const runtime = "nodejs"
export const maxDuration = 60

const LIMITS = { business: 120, website: 300, product: 200, note: 1000 } as const

function field(body: Record<string, unknown>, key: keyof typeof LIMITS): string {
  const raw = body[key]
  return (typeof raw === "string" ? raw : "").trim().slice(0, LIMITS[key])
}

/** Start a TinyFish brand-research job; the page polls GET /api/jobs/:id. */
export async function POST(req: NextRequest) {
  let body: Record<string, unknown>
  try {
    body = (await req.json()) as Record<string, unknown>
  } catch {
    return NextResponse.json({ error: "JSON body required" }, { status: 400 })
  }
  const business = field(body, "business")
  const website = field(body, "website")
  const product = field(body, "product")
  const note = field(body, "note")
  if (!business || !product) {
    return NextResponse.json({ error: "business and product are required" }, { status: 400 })
  }

  // No key: the page falls back to the simulated research flow.
  if (!(process.env.TINYFISH_API_KEY || "").trim()) {
    return NextResponse.json({ mock: true }, { status: 503 })
  }

  const jobId = newRunId()
  const now = new Date().toISOString()
  const job: ResearchJob = {
    id: jobId,
    type: "research",
    status: "running",
    step: 0,
    steps: [],
    sources: [],
    profile: null,
    created_at: now,
    updated_at: now,
  }
  try {
    writeJob(jobId, job)
    // `--flag=value` so user text starting with "-" isn't read as an option.
    spawnJob(
      [
        "-m",
        "product_placement.jobs",
        "brand-research",
        `--job-id=${jobId}`,
        `--business=${business}`,
        `--website=${website}`,
        `--product=${product}`,
        `--note=${note}`,
      ],
      { jobId }
    )
    return NextResponse.json({ jobId })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
