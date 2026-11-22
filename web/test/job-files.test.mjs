// Run with `npm test` (Node 22.18+ strips TypeScript types natively).
import assert from "node:assert/strict"
import { test } from "node:test"

import { jobFileType } from "../src/lib/job-files.ts"

test("serves existing job files with their types", () => {
  assert.equal(jobFileType("original.mp4"), "video/mp4")
  assert.equal(jobFileType("result.mp4"), "video/mp4")
  assert.equal(jobFileType("contact_sheet.jpg"), "image/jpeg")
})

test("serves research image candidates 1-3 as PNG", () => {
  for (const n of [1, 2, 3]) assert.equal(jobFileType(`candidate_${n}.png`), "image/png")
})

test("rejects everything else", () => {
  for (const name of [
    "candidate_0.png",
    "candidate_4.png",
    "candidate_1.jpg",
    "candidate_1.png.exe",
    "xcandidate_1.png",
    "status.json",
    "../status.json",
    "toString",
    "__proto__",
    "",
  ]) {
    assert.equal(jobFileType(name), null, name)
  }
})
