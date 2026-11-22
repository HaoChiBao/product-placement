import { NextResponse } from "next/server"

import { generationHealth } from "@/lib/generation"
import { pythonBin, repoRoot, dataRoot } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET() {
  const generation = generationHealth()
  return NextResponse.json({
    ok: true,
    service: "product-placement",
    python: pythonBin(),
    repoRoot: repoRoot(),
    dataDir: dataRoot(),
    mockHiggsfield: generation.mock,
    generation,
  })
}
