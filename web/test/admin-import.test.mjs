import assert from "node:assert/strict"
import { test } from "node:test"

import { adminImportErrorStatus, adminImportGate } from "../src/lib/admin-import.ts"

test("hides the import routes when the token is unset", () => {
  assert.deepEqual(adminImportGate("secret", undefined), { ok: false, status: 404 })
  assert.deepEqual(adminImportGate("secret", ""), { ok: false, status: 404 })
  assert.deepEqual(adminImportGate("secret", "   "), { ok: false, status: 404 })
})

test("rejects a missing or wrong admin token", () => {
  assert.deepEqual(adminImportGate(null, "secret"), { ok: false, status: 401 })
  assert.deepEqual(adminImportGate("nope", "secret"), { ok: false, status: 401 })
  assert.deepEqual(adminImportGate("secret-extra", "secret"), { ok: false, status: 401 })
})

test("accepts the admin token in constant time", () => {
  assert.deepEqual(adminImportGate("secret", "secret"), { ok: true })
})

test("maps import failures to HTTP status", () => {
  assert.equal(adminImportErrorStatus("episode not found: abc"), 404)
  assert.equal(adminImportErrorStatus("video_s3_key required"), 400)
  assert.equal(adminImportErrorStatus("end_s must be greater than start_s"), 400)
  assert.equal(adminImportErrorStatus("invalid JSON: nope"), 400)
  assert.equal(adminImportErrorStatus("database is down"), 500)
})
