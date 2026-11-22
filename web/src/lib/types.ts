export type Region = { x: number; y: number; w: number; h: number }

export type BrandFit = {
  prefer_scores?: Record<string, number>
  avoid_scores?: Record<string, number>
  class_boost?: number
  aspect_fit?: number
  clip_prefer?: number
  clip_avoid?: number
  vetoed?: boolean
  veto_reason?: string | null
  reason?: string
  total?: number
}

export type Moment = {
  start_s: number
  end_s: number
  frame: string
  frame_w: number
  frame_h: number
  region: Region
  whats_there: string
  score: number
  reason: string
  brand_fit?: BrandFit | null
  frameUrl?: string
  clipUrl?: string
}

export type BrandRules = {
  prefer: string[]
  avoid: string[]
  placement: string
  min_area: number
}

export type Brand = {
  id: string
  name: string
  brief: string
  assets: string[]
  rules: BrandRules
  asset_urls?: string[]
}

export type SuggestRun = {
  runId: string
  video: string
  n: number
  window_s: number
  duration_s: number
  elapsed_s?: number
  moments: Moment[]
  contact_sheet?: string
  brand_id?: string | null
  vetoed?: { start_s: number; end_s: number; reason: string }[]
}

export type JobStatus = {
  id: string
  type: "suggest" | "swap" | "research"
  status: "queued" | "running" | "completed" | "failed"
  progress?: string
  error?: string
  created_at?: string
  updated_at?: string
  elapsed_s?: number
  estimated_cost_usd?: number
  mock?: boolean
  result?: Record<string, unknown>
}

/** Seedance 720p list price from Higgsfield /estimate. */
export const SEEDANCE_USD_PER_S = 0.3264
export const MAX_SWAP_INPUT_S = 5

export function estimateSwapCostUsd(durationS: number): number {
  const billed = Math.max(1, Math.ceil(Math.max(0, durationS) - 1e-9))
  return Math.round(billed * SEEDANCE_USD_PER_S * 10000) / 10000
}
