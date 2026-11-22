import assert from "node:assert/strict"
import { test } from "node:test"

import {
  groupPlacementSlots,
  choiceStorageId,
  choicesForScope,
  readVersionChoices,
  versionAtTime,
  versionChipLabel,
  writeVersionChoice,
} from "../src/lib/placement-slots.ts"

function clip(id, brand, start, end, extra = {}) {
  return {
    id,
    brand_id: extra.brand_id || brand.toLowerCase().replace(/\s+/g, "-"),
    brand_name: brand,
    start_s: start,
    end_s: end,
    clip_url: `https://cdn.example/${id}.mp4`,
    version_label: extra.version_label ?? null,
    slot_key: extra.slot_key ?? null,
  }
}

test("groups versions on one window and plays the first by default", () => {
  const slots = groupPlacementSlots([
    clip("ha-2", "Daisy", 107.87, 113.87, { version_label: "Daisy v1" }),
    clip("ha-3", "Swiss Miss", 107.87, 113.87, { version_label: "Swiss Miss" }),
    clip("ha-1", "AT&T", 104.701, 107.701),
  ])
  assert.equal(slots.length, 2)
  assert.deepEqual(
    slots.map((slot) => slot.versions.map((version) => version.id)),
    [["ha-1"], ["ha-2", "ha-3"]]
  )

  const hit = versionAtTime(slots, 110, {})
  assert.equal(hit.version.id, "ha-2")
  assert.deepEqual(
    hit.options.map((version) => version.id),
    ["ha-2", "ha-3"]
  )
  assert.equal(versionChipLabel(hit.options[0]), "Daisy · Daisy v1")
  assert.equal(versionChipLabel(hit.options[1]), "Swiss Miss · Swiss Miss")
})

test("remembers a choice per slot and still lets brandId force a brand", () => {
  const daisy = clip("ha-2", "Daisy", 107.87, 113.87, {
    version_label: "v1",
    slot_key: "fridge-tub",
  })
  const swiss = clip("ha-3", "Swiss Miss", 107.87, 113.87, {
    version_label: "v1",
    slot_key: "fridge-tub",
  })
  const phone = clip("ha-1", "AT&T", 104.701, 107.701, { slot_key: "phone" })
  const slots = groupPlacementSlots([phone, daisy, swiss])
  const fridge = slots.find((slot) => slot.id === "key:fridge-tub")
  assert.ok(fridge)
  assert.deepEqual(
    fridge.versions.map((version) => version.id),
    ["ha-2", "ha-3"]
  )

  const choice = writeVersionChoice({}, fridge.id, "ha-3")
  assert.equal(versionAtTime(slots, 110, choice).version.id, "ha-3")
  assert.equal(versionAtTime(slots, 105, choice).version.id, "ha-1")
  assert.equal(versionAtTime(slots, 110, choice, daisy.brand_id).version.id, "ha-2")
  assert.equal(versionAtTime(slots, 110, choice, daisy.brand_id).options.length, 1)
})

test("slot_key separates overlapping objects and joins a shared slot", () => {
  const slots = groupPlacementSlots([
    clip("mug-a", "Daisy", 10, 16, { slot_key: "mug" }),
    clip("mug-b", "Pepsi", 10.4, 16, { slot_key: "mug", version_label: "v2" }),
    clip("phone", "AT&T", 10, 16, { slot_key: "phone" }),
  ])
  assert.deepEqual(
    slots.map((slot) => [slot.id, slot.versions.map((version) => version.id)]),
    [
      ["key:mug", ["mug-a", "mug-b"]],
      ["key:phone", ["phone"]],
    ]
  )
  assert.equal(versionAtTime(slots, 12, { "key:mug": "mug-b" }).version.id, "mug-b")
})

test("overlapping windows without a slot key are one slot; a touch is not", () => {
  const overlap = groupPlacementSlots([
    clip("a", "Coke", 10, 20, { version_label: "v1" }),
    clip("b", "Pepsi", 18, 24, { version_label: "v2" }),
  ])
  assert.equal(overlap.length, 1)
  assert.equal(versionAtTime(overlap, 19, {}).version.id, "a")
  assert.equal(versionAtTime(overlap, 22, {}).version.id, "b")

  const touch = groupPlacementSlots([
    clip("a", "Coke", 10, 20),
    clip("b", "Pepsi", 20, 30),
  ])
  assert.equal(touch.length, 2)
  assert.equal(versionAtTime(touch, 20, {}).version.id, "b")
  assert.equal(versionAtTime(touch, 19.9, {}).version.id, "a")
})

test("keeps a saved version on its episode", () => {
  const saved = writeVersionChoice({}, choiceStorageId("home-alone:1:1", "win:107870"), "ha-3")
  assert.equal(choicesForScope(saved, "home-alone:1:1")["win:107870"], "ha-3")
  assert.deepEqual(choicesForScope(saved, "other-show:1:1"), {})
})

test("ignores a broken saved choice", () => {
  assert.deepEqual(readVersionChoices(null), {})
  assert.deepEqual(readVersionChoices("nope"), {})
  assert.deepEqual(readVersionChoices('{"slot":"p1","bad":1}'), { slot: "p1" })
  const slots = groupPlacementSlots([
    clip("a", "Coke", 1, 2, { version_label: "v1" }),
    clip("b", "Pepsi", 1, 2, { version_label: "v2" }),
  ])
  assert.equal(versionAtTime(slots, 1, { [slots[0].id]: "missing" }).version.id, "a")
})
