import { NextRequest, NextResponse } from "next/server"

import { readJob, readRun, withUrls, type SuggestRun } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ jobId: string }> }
) {
  const { jobId } = await ctx.params
  const job = readJob(jobId)
  if (!job) {
    return NextResponse.json({ error: "job not found" }, { status: 404 })
  }

  if (job.type === "suggest" && job.status === "completed") {
    const run = readRun(jobId)
    if (run) {
      return NextResponse.json({ ...job, result: withUrls(run) })
    }
  }

  if (job.type === "swap" && job.status === "completed" && job.result) {
    const result = { ...job.result } as Record<string, unknown>
    result.original_url = `/api/jobs/${jobId}/file/original.mp4`
    result.result_url = `/api/jobs/${jobId}/file/result.mp4`
    result.download_url = `/api/jobs/${jobId}/file/result.mp4?download=1`
    return NextResponse.json({ ...job, result })
  }

  return NextResponse.json(job)
}

export type { SuggestRun }
