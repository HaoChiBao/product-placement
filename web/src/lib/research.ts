/**
 * Brand research. `POST /api/research` starts a TinyFish job (search, fetch, one
 * agent run) that the page polls via `/api/jobs/:id`. Without TINYFISH_API_KEY
 * (or with `?mock=1`) the page simulates it with `@mock/research`:
 * `src/mock/research.local.ts` when present (gitignored), else
 * `src/mock/research.example.ts` (see next.config.ts).
 */
import { MOCK_RESEARCH } from "@mock/research"

export type ResearchInput = {
  business: string
  website: string
  product: string
  note: string
}

export type ResearchStep = { label: string; detail: string }
export type ResearchSource = { kind: "search" | "fetch"; url: string; ms: number }

export type BrandProfile = {
  business: string
  website: string
  product: string
  productFacts: string
  location: string
  category: string
  tone: string
  audience: string
  colors: string[]
  goodScenes: string[]
  avoidScenes: string[]
  /** Words `parse_brief` maps to detector classes, e.g. "cups", "tables". */
  prefer: string[]
  avoid: string[]
}

/** A cleaned product image the research job saved as `candidate_<n>.png` (best first). */
export type ResearchImage = {
  name: string
  source: "agent" | "shopify" | "img" | "retailer"
  url: string
  /** Retailer the image was found on (source "retailer"), e.g. "walmart.com". */
  host?: string
  /** Named or listed as a pack (e.g. "12-pack"); ranked below single units. */
  multipack?: boolean
  w: number
  h: number
  /** True when the background was removed (transparent PNG). */
  cutout: boolean
  score: number
}

/** `DATA_DIR/jobs/<id>/status.json` written by `product_placement.jobs brand-research`. */
export type ResearchJob = {
  id: string
  type: "research"
  status: "queued" | "running" | "completed" | "failed"
  /** Index into `steps`. */
  step: number
  steps: ResearchStep[]
  sources: ResearchSource[]
  detail?: string
  profile: BrandProfile | null
  images?: ResearchImage[]
  error?: string
  tinyfish_run_id?: string | null
  streaming_url?: string | null
  cached?: boolean
  agent_steps?: number
  elapsed_s?: number
  created_at?: string
  updated_at?: string
}

export type MockResearch = {
  defaults: ResearchInput
  steps: (input: ResearchInput) => ResearchStep[]
  sources: (input: ResearchInput) => ResearchSource[]
  profile: (input: ResearchInput) => BrandProfile
}

export const research: MockResearch = MOCK_RESEARCH

/** Brief text for POST /api/catalog/brands; the backend parses it into prefer/avoid rules. */
export function briefFor(p: BrandProfile, note: string): string {
  const parts = [
    `${p.product}.`,
    p.prefer.length ? `Prefer ${p.prefer.join(", ")}.` : "",
    p.avoid.length ? `Avoid ${p.avoid.join(", ")}.` : "",
    note.trim(),
  ]
  return parts.filter(Boolean).join(" ")
}

const POLL_MS = 1000
const TIMEOUT_MS = 4 * 60 * 1000

// One start request per input + attempt, so a remounted effect (React Strict
// Mode in dev) reuses the job instead of paying for a second agent run.
const starts = new Map<string, Promise<{ jobId: string } | { mock: true }>>()

/** Start a research job, or `{ mock: true }` when the server has no TinyFish key. */
export function startResearch(input: ResearchInput, attempt: number) {
  const key = JSON.stringify([input, attempt])
  let started = starts.get(key)
  if (!started) {
    started = (async () => {
      const res = await fetch("/api/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      })
      const body = (await res.json().catch(() => ({}))) as { jobId?: string; mock?: boolean; error?: string }
      if (res.status === 503 && body.mock) return { mock: true as const }
      if (!res.ok || !body.jobId) throw new Error(body.error || `Couldn’t start research (${res.status})`)
      return { jobId: body.jobId }
    })()
    starts.set(key, started)
    started.catch(() => starts.delete(key))
  }
  return started
}

export function forgetResearch(input: ResearchInput, attempt: number) {
  starts.delete(JSON.stringify([input, attempt]))
}

/** Poll until the job completes (resolves) or fails / times out (throws). Returns null if cancelled. */
export async function pollResearch(
  jobId: string,
  onUpdate: (job: ResearchJob) => void,
  isCancelled: () => boolean
): Promise<ResearchJob | null> {
  const deadline = Date.now() + TIMEOUT_MS
  while (!isCancelled()) {
    if (Date.now() > deadline) throw new Error("Research timed out after 4 minutes.")
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" })
      if (res.ok) {
        const job = (await res.json()) as ResearchJob
        if (isCancelled()) return null
        onUpdate(job)
        if (job.status === "completed") {
          if (job.profile) return job
          throw new Error("Research finished without a profile.")
        }
        if (job.status === "failed") throw new Error(job.error || "Research failed.")
      }
    } catch (e) {
      if (e instanceof TypeError) {
        // network blip; keep polling until the deadline
      } else {
        throw e
      }
    }
    await new Promise((r) => setTimeout(r, POLL_MS))
  }
  return null
}
