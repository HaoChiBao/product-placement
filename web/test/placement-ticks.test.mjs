import assert from "node:assert/strict"
import { test } from "node:test"

import { placementTickBoxes } from "../src/lib/placement-ticks.ts"

test("places a span from start to end on the progress bar", () => {
  const [tick] = placementTickBoxes(
    [{ id: "ha-1", brand: "AT&T", start_s: 10, end_s: 20 }],
    100
  )
  assert.equal(tick.leftPct, 10)
  assert.equal(tick.widthPct, 10)
  assert.equal(tick.spanned, true)
  assert.equal(tick.brand, "AT&T")
})

test("uses a start tick when the window is too narrow to read", () => {
  const [tick] = placementTickBoxes(
    [{ id: "short", brand: "Zippo", start_s: 10, end_s: 10.2 }],
    100
  )
  assert.equal(tick.leftPct, 10)
  assert.equal(tick.widthPct, 0)
  assert.equal(tick.spanned, false)
})

test("sample windows sit at their episode timestamps", () => {
  const duration = 145.45
  const boxes = placementTickBoxes(
    [
      { id: "HA-4", brand: "Sherwin-Williams", start_s: 62.668, end_s: 64.668 },
      { id: "HA-1", brand: "AT&T", start_s: 104.701, end_s: 107.701 },
    ],
    duration
  )
  assert.ok(Math.abs(boxes[0].leftPct - (62.668 / duration) * 100) < 1e-9)
  assert.ok(Math.abs(boxes[0].widthPct - (2 / duration) * 100) < 1e-9)
  assert.equal(boxes[0].spanned, true)
  assert.ok(Math.abs(boxes[1].leftPct - (104.701 / duration) * 100) < 1e-9)
  assert.equal(boxes[1].widthPct, ((107.701 - 104.701) / duration) * 100)
  assert.equal(boxes[1].brand, "AT&T")
})

test("clamps to the episode and skips an unknown duration", () => {
  const [tick] = placementTickBoxes(
    [{ id: "over", brand: " 3M Post-it ", start_s: -2, end_s: 500 }],
    100
  )
  assert.equal(tick.leftPct, 0)
  assert.equal(tick.widthPct, 100)
  assert.equal(tick.brand, "3M Post-it")
  assert.deepEqual(
    placementTickBoxes([{ id: "x", brand: "AT&T", start_s: 1, end_s: 2 }], 0),
    []
  )
})
