import fs from "fs"
import path from "path"
import { NextRequest, NextResponse } from "next/server"

import { readRun, runDir } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ runId: string }> }
) {
  const { runId } = await ctx.params
  const run = readRun(runId)
  if (!run) {
    return NextResponse.json({ error: "run not found" }, { status: 404 })
  }
  // Ensure source pointer exists when loading a copied artifact run.
  const source = path.join(runDir(runId), "source.txt")
  if (!fs.existsSync(source) && run.video) {
    fs.writeFileSync(source, run.video)
  }
  return NextResponse.json(run)
}
