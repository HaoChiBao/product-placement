import assert from "node:assert/strict"
import { test } from "node:test"

import { imageHref, researchDone, researchHref, sameImage, sameInput } from "../src/lib/flow.ts"

const input = { business: "pepsi", website: "pepsi.ca", product: "pepsi bottle", note: "" }

test("same input ignores surrounding spaces; any edit is a new run", () => {
  assert.ok(sameInput(input, { ...input, business: " pepsi " }))
  assert.ok(!sameInput(input, { ...input, product: "pepsi can" }))
  assert.ok(!sameInput(input, { ...input, note: "no kids" }))
  assert.ok(!sameInput(input, null))
})

test("research reopens only once it finished", () => {
  assert.ok(!researchDone(null))
  assert.ok(!researchDone({ input }))
  assert.ok(researchDone({ input, jobId: "abc" }))
  assert.ok(researchDone({ input, mockProfile: {} }))
})

test("reopen links carry the input and the finished job", () => {
  const flow = { input, jobId: "c0a979ec-efb" }
  assert.equal(researchHref(flow), "/research?business=pepsi&website=pepsi.ca&product=pepsi+bottle&job=c0a979ec-efb")
  assert.equal(imageHref(flow), "/product-image?business=pepsi&website=pepsi.ca&product=pepsi+bottle&job=c0a979ec-efb")
})

test("an unchanged image choice isn't saved again", () => {
  assert.ok(sameImage({ kind: "candidate", name: "candidate_1.png" }, { kind: "candidate", name: "candidate_1.png" }))
  assert.ok(!sameImage({ kind: "candidate", name: "candidate_2.png" }, { kind: "candidate", name: "candidate_1.png" }))
  assert.ok(sameImage({ kind: "none" }, { kind: "none" }))
  assert.ok(sameImage({ kind: "own" }, { kind: "own", assetUrl: "/api/media/x.png" }))
  assert.ok(!sameImage({ kind: "own" }, { kind: "none" }))
  assert.ok(!sameImage(undefined, { kind: "none" }))
})
