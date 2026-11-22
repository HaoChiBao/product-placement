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

test("merges overlapping versions into one marker with a count", () => {
  const [tick] = placementTickBoxes(
    [
      { id: "ha-2", brand: "Daisy", start_s: 107.87, end_s: 113.87 },
      { id: "ha-3", brand: "Swiss Miss", start_s: 107.87, end_s: 113.87 },
    ],
    145.45
  )
  assert.equal(tick.count, 2)
  assert.equal(tick.brand, "Daisy, Swiss Miss")
  assert.equal(tick.label, "Daisy, Swiss Miss · 2")
  assert.equal(tick.spanned, true)
  assert.ok(Math.abs(tick.leftPct - (107.87 / 145.45) * 100) < 1e-9)
  assert.ok(Math.abs(tick.widthPct - (6 / 145.45) * 100) < 1e-9)
})

test("merges windows that touch and keeps a gap split", () => {
  const touching = placementTickBoxes(
    [
      { id: "a", brand: "Coke", start_s: 10, end_s: 20 },
      { id: "b", brand: "Coke", start_s: 20, end_s: 30 },
    ],
    100
  )
  assert.equal(touching.length, 1)
  assert.equal(touching[0].leftPct, 10)
  assert.equal(touching[0].widthPct, 20)
  assert.equal(touching[0].count, 2)
  assert.equal(touching[0].label, "Coke · 2")

  const gapped = placementTickBoxes(
    [
      { id: "a", brand: "Coke", start_s: 10, end_s: 20 },
      { id: "b", brand: "Pepsi", start_s: 20.05, end_s: 30 },
    ],
    100
  )
  assert.equal(gapped.length, 2)
  assert.equal(gapped[0].count, 1)
  assert.equal(gapped[1].brand, "Pepsi")
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
