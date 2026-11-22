export const SLOT_START_MATCH_S = 0.001

export const VERSION_CHOICE_STORAGE_KEY = "pp-placement-versions"

export type SlotPlacement = {
  id: string
  brand_id?: string | null
  brand_name?: string | null
  version_label?: string | null
  slot_key?: string | null
  start_s: number
  end_s: number
  clip_url?: string | null
}

export type PlacementSlot<T extends SlotPlacement = SlotPlacement> = {
  id: string
  versions: T[]
}

export type VersionHit<T extends SlotPlacement = SlotPlacement> = {
  slot: PlacementSlot<T>
  version: T
  options: T[]
}

function explicitSlotKey(placement: SlotPlacement) {
  return (placement.slot_key || "").trim()
}

function overlaps(a: SlotPlacement, b: SlotPlacement) {
  return a.start_s < b.end_s && b.start_s < a.end_s
}

function sameStart(a: SlotPlacement, b: SlotPlacement) {
  return Math.abs(a.start_s - b.start_s) <= SLOT_START_MATCH_S
}

function clusterLoose<T extends SlotPlacement>(loose: T[]): T[][] {
  const parent = loose.map((_, index) => index)
  const find = (index: number) => {
    let cursor = index
    while (parent[cursor] !== cursor) {
      parent[cursor] = parent[parent[cursor]]
      cursor = parent[cursor]
    }
    return cursor
  }
  for (let i = 0; i < loose.length; i++) {
    for (let j = i + 1; j < loose.length; j++) {
      if (sameStart(loose[i], loose[j]) || overlaps(loose[i], loose[j])) {
        parent[find(i)] = find(j)
      }
    }
  }
  const groups = new Map<number, T[]>()
  loose.forEach((placement, index) => {
    const root = find(index)
    const list = groups.get(root)
    if (list) list.push(placement)
    else groups.set(root, [placement])
  })
  return [...groups.values()]
}

export function groupPlacementSlots<T extends SlotPlacement>(
  placements: T[]
): PlacementSlot<T>[] {
  const keyed = new Map<string, T[]>()
  const loose: T[] = []
  for (const placement of placements) {
    const key = explicitSlotKey(placement)
    if (key) {
      const list = keyed.get(key)
      if (list) list.push(placement)
      else keyed.set(key, [placement])
    } else {
      loose.push(placement)
    }
  }

  const slots: PlacementSlot<T>[] = []
  for (const [key, versions] of keyed) {
    slots.push({ id: `key:${key}`, versions })
  }
  for (const versions of clusterLoose(loose)) {
    const start = Math.min(...versions.map((version) => version.start_s))
    slots.push({ id: `win:${Math.round(start * 1000)}`, versions })
  }
  slots.sort((a, b) => {
    const aStart = Math.min(...a.versions.map((version) => version.start_s))
    const bStart = Math.min(...b.versions.map((version) => version.start_s))
    return aStart - bStart
  })
  return slots
}

export function versionsCovering<T extends SlotPlacement>(
  slot: PlacementSlot<T>,
  time: number,
  brandId?: string | null
): T[] {
  const picked = (brandId || "").trim()
  let versions = slot.versions.filter(
    (version) => version.clip_url && time >= version.start_s && time < version.end_s
  )
  if (picked) versions = versions.filter((version) => version.brand_id === picked)
  return versions
}

export function versionAtTime<T extends SlotPlacement>(
  slots: PlacementSlot<T>[],
  time: number,
  choice?: Readonly<Record<string, string>> | null,
  brandId?: string | null
): VersionHit<T> | null {
  for (const slot of slots) {
    const options = versionsCovering(slot, time, brandId)
    if (!options.length) continue
    const chosenId = choice?.[slot.id]
    const version = options.find((item) => item.id === chosenId) || options[0]
    return { slot, version, options }
  }
  return null
}

export function versionChipLabel(placement: {
  brand_name?: string | null
  version_label?: string | null
}) {
  const brand = (placement.brand_name || "").trim()
  const label = (placement.version_label || "").trim()
  if (brand && label) return `${brand} · ${label}`
  return brand || label || "Version"
}

export function readVersionChoices(raw: string | null): Record<string, string> {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {}
    const choices: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "string" && value) choices[key] = value
    }
    return choices
  } catch {
    return {}
  }
}

export function choiceStorageId(scope: string, slotId: string) {
  const trimmed = scope.trim()
  return trimmed ? `${trimmed}:${slotId}` : slotId
}

export function choicesForScope(
  choices: Readonly<Record<string, string>>,
  scope: string
): Record<string, string> {
  const prefix = scope.trim()
  if (!prefix) return { ...choices }
  const needle = `${prefix}:`
  const scoped: Record<string, string> = {}
  for (const [key, value] of Object.entries(choices)) {
    if (key.startsWith(needle)) scoped[key.slice(needle.length)] = value
  }
  return scoped
}

export function writeVersionChoice(
  choices: Readonly<Record<string, string>>,
  slotId: string,
  placementId: string
): Record<string, string> {
  return { ...choices, [slotId]: placementId }
}
