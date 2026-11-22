import assert from "node:assert/strict"
import { test } from "node:test"

import { playbackEnd, playbackOrigin } from "../src/lib/clip-window.ts"

test("plays a clamped Wan clip on its own window", () => {
  assert.equal(playbackOrigin(0, 15), 15)
  assert.equal(playbackEnd(40, 25), 25)
})

test("falls back to the moment when the placement has no clip window", () => {
  assert.equal(playbackOrigin(10, null), 10)
  assert.equal(playbackOrigin(10, undefined), 10)
  assert.equal(playbackEnd(40, null), 40)
  assert.equal(playbackOrigin(0, 0), 0)
})
