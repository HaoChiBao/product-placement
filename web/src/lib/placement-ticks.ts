export const PLACEMENT_TICK_MIN_SPAN = 0.008

export type PlacementTickInput = {
  id: string
  brand?: string | null
  start_s: number
  end_s: number
}

export type PlacementTickBox = {
  id: string
  brand: string
  leftPct: number
  widthPct: number
  spanned: boolean
}

function clamp(n: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, n))
}

/** Tick at start_s. Width covers start..end when that span is wide enough to read. */
export function placementTickBoxes(
  placements: PlacementTickInput[],
  duration: number
): PlacementTickBox[] {
  if (!(duration > 0) || !Number.isFinite(duration)) return []
  return placements.map((placement) => {
    const start = clamp(
      Number.isFinite(placement.start_s) ? placement.start_s : 0,
      0,
      duration
    )
    const end = clamp(
      Number.isFinite(placement.end_s) ? Math.max(placement.end_s, start) : start,
      0,
      duration
    )
    const leftPct = (start / duration) * 100
    const spanPct = ((end - start) / duration) * 100
    const spanned = spanPct >= PLACEMENT_TICK_MIN_SPAN * 100
    return {
      id: placement.id,
      brand: (placement.brand || "").trim(),
      leftPct,
      widthPct: spanned ? spanPct : 0,
      spanned,
    }
  })
}
