import { NextRequest, NextResponse } from "next/server"
import { spawnSync } from "child_process"

import { pythonBin, pythonEnv, repoRoot } from "@/lib/runs"

export const runtime = "nodejs"

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { brief?: string }
    const brief = String(body.brief || "")
    const script = `
import json, sys
from product_placement.brand import parse_brief
rules = parse_brief(sys.argv[1])
print(json.dumps(rules.to_dict()))
`
    const res = spawnSync(pythonBin(), ["-c", script, brief], {
      cwd: repoRoot(),
      env: pythonEnv(),
      encoding: "utf8",
    })
    if (res.status !== 0) {
      return NextResponse.json(
        { error: (res.stderr || res.stdout || "parse failed").trim() },
        { status: 500 }
      )
    }
    return NextResponse.json(JSON.parse(res.stdout))
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}
