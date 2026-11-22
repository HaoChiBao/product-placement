/**
 * Brand research. The TinyFish web agent isn't wired up yet, so steps, sources
 * and the profile come from `@mock/research`: `src/mock/research.local.ts` when
 * present (gitignored), else `src/mock/research.example.ts` (see next.config.ts).
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
