import { NextRequest, NextResponse } from "next/server"

import { catalogCmd, ensureCatalogDb } from "@/lib/catalog"
import { adminImportErrorStatus, adminImportGate } from "@/lib/admin-import"

export const runtime = "nodejs"
export const maxDuration = 60

function blocked(req: NextRequest) {
  const gate = adminImportGate(req.headers.get("x-admin-token"))
  if (gate.ok) return null
  const error = gate.status === 404 ? "not found" : "unauthorized"
  return NextResponse.json({ error }, { status: gate.status })
}

export async function POST(req: NextRequest) {
  const deny = blocked(req)
  if (deny) return deny
  try {
    ensureCatalogDb()
    let body: unknown
    try {
      body = await req.json()
    } catch {
      return NextResponse.json({ error: "invalid JSON" }, { status: 400 })
    }
    const result = catalogCmd(
      ["import-moments", "--payload", "-"],
      JSON.stringify(body)
    )
    return NextResponse.json(result)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return NextResponse.json(
      { error: message },
      { status: adminImportErrorStatus(message) }
    )
  }
}
