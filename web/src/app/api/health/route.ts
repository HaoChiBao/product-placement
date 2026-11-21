import { NextResponse } from "next/server"

import { pythonBin, repoRoot, dataRoot } from "@/lib/runs"

export const runtime = "nodejs"

export async function GET() {
  return NextResponse.json({
    ok: true,
    service: "product-placement",
    python: pythonBin(),
    repoRoot: repoRoot(),
    dataDir: dataRoot(),
    mockHiggsfield: ["1", "true", "yes", "on"].includes(
      (process.env.MOCK_HIGGSFIELD || "").trim().toLowerCase()
    ),
  })
}
