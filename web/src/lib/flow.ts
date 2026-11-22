/**
 * The brand onboarding run in this browser (localStorage), so finished steps can
 * be reopened from the top bar to see what was entered, found and chosen,
 * without starting another research run. Nothing re-runs unless the input is
 * edited; nothing is re-saved unless the image choice changes.
 */
import type { BrandProfile, ResearchInput } from "@/lib/research"

/** The image saved with the brand: a research candidate, the user's own upload, or none. */
export type FlowImage =
  | { kind: "candidate"; name: string }
  | { kind: "own"; assetUrl?: string | null; fileName?: string }
  | { kind: "none" }

export type Flow = {
  input: ResearchInput
  /** Finished research job (live TinyFish). */
  jobId?: string
  /** Finished simulated research (no TinyFish key): there's no job to reload. */
  mockProfile?: BrandProfile
  image?: FlowImage
  brandId?: string
}

const KEY = "inset.flow"
/** Fired on window when the flow changes, so the top bar can update its step links. */
export const FLOW_EVENT = "inset:flow"

export function getFlow(): Flow | null {
  try {
    const raw = window.localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as Flow) : null
  } catch {
    return null
  }
}

export function setFlow(flow: Flow | null) {
  try {
    if (flow) window.localStorage.setItem(KEY, JSON.stringify(flow))
    else window.localStorage.removeItem(KEY)
  } catch {
    /* storage blocked: steps just won't reopen */
  }
  window.dispatchEvent(new Event(FLOW_EVENT))
}

const norm = (s: string) => (s || "").trim()

export function sameInput(a?: ResearchInput | null, b?: ResearchInput | null): boolean {
  if (!a || !b) return false
  return (["business", "website", "product", "note"] as const).every((k) => norm(a[k]) === norm(b[k]))
}

/** True once research finished for this flow, so it can be reopened without a new run. */
export function researchDone(flow: Flow | null): boolean {
  return Boolean(flow && (flow.jobId || flow.mockProfile))
}

function query(flow: Flow): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(flow.input)) if (v) q.set(k, v)
  if (flow.jobId) q.set("job", flow.jobId)
  return q.toString()
}

export function researchHref(flow: Flow): string {
  return `/research?${query(flow)}`
}

export function imageHref(flow: Flow): string {
  return `/product-image?${query(flow)}`
}

export function sameImage(a?: FlowImage | null, b?: FlowImage | null): boolean {
  if (!a || !b || a.kind !== b.kind) return false
  return a.kind !== "candidate" || a.name === (b as { name: string }).name
}
