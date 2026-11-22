import assert from "node:assert/strict"
import { test } from "node:test"

import { NextRequest } from "next/server"

import { POST } from "../src/app/api/admin/placements/delete/route.ts"

test("delete route rejects a missing or bad admin token", async () => {
  process.env.ADMIN_IMPORT_TOKEN = "secret"
  const url = "http://localhost/api/admin/placements/delete"
  const body = JSON.stringify({ ids: ["placement-1"] })

  const missing = await POST(
    new NextRequest(url, {
      method: "POST",
      body,
      headers: { "content-type": "application/json" },
    })
  )
  assert.equal(missing.status, 401)
  assert.deepEqual(await missing.json(), { error: "unauthorized" })

  const bad = await POST(
    new NextRequest(url, {
      method: "POST",
      body,
      headers: { "content-type": "application/json", "x-admin-token": "nope" },
    })
  )
  assert.equal(bad.status, 401)
  assert.deepEqual(await bad.json(), { error: "unauthorized" })
})
