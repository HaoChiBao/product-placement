export const PLACEMENT_TICK_MIN_SPAN = 0.008
const TICK_TOUCH_S = 0.001

export type PlacementTickInput = {
  id: string
  brand?: string | null
  start_s: number
  end_s: number
}

export type PlacementTickBox = {
  id: string
  brand: string
  label: string
  count: number
  leftPct: number
  widthPct: number
  spanned: boolean
}

function clamp(n: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, n))
}

function windowOf(placement: PlacementTickInput, duration: number) {
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
  return { start, end }
}

function brandsOf(items: PlacementTickInput[]) {
  const seen = new Set<string>()
  const brands: string[] = []
  for (const item of items) {
    const brand = (item.brand || "").trim()
    if (!brand || seen.has(brand)) continue
    seen.add(brand)
    brands.push(brand)
  }
  return brands.join(", ")
}

function tickLabel(brand: string, count: number) {
  if (count > 1) return brand ? `${brand} · ${count}` : `${count}`
  return brand
}

export function placementTickBoxes(
  placements: PlacementTickInput[],
  duration: number
): PlacementTickBox[] {
  if (!(duration > 0) || !Number.isFinite(duration)) return []
  const ordered = placements
    .map((placement, index) => ({ placement, index, ...windowOf(placement, duration) }))
    .sort((a, b) => a.start - b.start || a.index - b.index)

  const groups: Array<{ start: number; end: number; items: PlacementTickInput[] }> = []
  for (const item of ordered) {
    const last = groups[groups.length - 1]
    if (last && item.start <= last.end + TICK_TOUCH_S) {
      last.end = Math.max(last.end, item.end)
      last.items.push(item.placement)
    } else {
      groups.push({ start: item.start, end: item.end, items: [item.placement] })
    }
  }

  return groups.map((group) => {
    const leftPct = (group.start / duration) * 100
    const spanPct = ((group.end - group.start) / duration) * 100
    const spanned = spanPct >= PLACEMENT_TICK_MIN_SPAN * 100
    const brand = brandsOf(group.items)
    const count = group.items.length
    return {
      id: group.items.map((item) => item.id).join("+"),
      brand,
      label: tickLabel(brand, count),
      count,
      leftPct,
      widthPct: spanned ? spanPct : 0,
      spanned,
    }
  })
}
