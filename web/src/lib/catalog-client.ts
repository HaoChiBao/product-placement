/** Browser-side helpers for the /api/catalog routes plus the brand session kept in localStorage. */
import type { BrandProfile } from "@/lib/research"

export type VideoSource = "test" | "hosted" | "all"

export type Title = {
  id: string
  name: string
  slug: string
  kind: string
  synopsis?: string
  attribution?: string
  poster_key?: string | null
  source?: "test" | "hosted" | string
}

export type Episode = {
  id: string
  title_id: string
  name: string
  season: number
  episode_number: number
  video_key?: string | null
  poster_key?: string | null
  duration_s?: number | null
  suggest_status?: string | null
}

export type CatalogMoment = {
  id: string
  episode_id: string
  start_s: number
  end_s: number
  region?: { x: number; y: number; w: number; h: number }
  whats_there?: string
  score?: number
  frame_key?: string | null
  frame_w?: number
  frame_h?: number
  brand_fit?: { total?: number; vetoed?: boolean; reason?: string } | null
}

export type Placement = {
  id: string
  brand_id: string
  moment_id: string
  episode_id: string
  status: "approved" | "rejected" | "queued" | "rendering" | "ready" | "failed"
  clip_key?: string | null
  error?: string | null
  updated_at?: string
}

export type Campaign = {
  id: string
  brand_id: string
  title_id: string
  episode_id: string
  status: "draft" | "in_progress" | "live"
  created_at?: string
  updated_at?: string
}

export type GenerationInfo = {
  ready: boolean
  mock: boolean
  keysConfigured: boolean
  reason?: string | null
}

export type Video = Episode & { title: Title }

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((data as { error?: string }).error || `${url} failed (${res.status})`)
  return data as T
}

/** Episodes are scanned for spots in the background; "ready" means suggest finished. */
export function isScanned(v: Pick<Episode, "suggest_status">): boolean {
  return ["ready", "completed", "done"].includes((v.suggest_status || "").toLowerCase())
}

export function mediaUrl(key?: string | null): string | null {
  if (!key) return null
  return `/api/media/${key.split("/").map(encodeURIComponent).join("/")}`
}

const VIDEO_SOURCE_KEY = "inset.videoSource"

export function parseVideoSource(raw: string | null | undefined): VideoSource | null {
  const v = (raw || "").trim().toLowerCase()
  return v === "test" || v === "hosted" || v === "all" ? v : null
}

/** Query param → localStorage → null (API then uses VIDEO_SOURCE / all). */
export function readVideoSource(): VideoSource | null {
  if (typeof window === "undefined") return null
  const fromQuery = parseVideoSource(new URLSearchParams(window.location.search).get("source"))
  if (fromQuery) return fromQuery
  try {
    return parseVideoSource(window.localStorage.getItem(VIDEO_SOURCE_KEY))
  } catch {
    return null
  }
}

export function writeVideoSource(source: VideoSource) {
  try {
    window.localStorage.setItem(VIDEO_SOURCE_KEY, source)
  } catch {
    /* storage blocked */
  }
  if (typeof window === "undefined") return
  const url = new URL(window.location.href)
  if (source === "all") url.searchParams.delete("source")
  else url.searchParams.set("source", source)
  window.history.replaceState({}, "", url.toString())
}

export async function listVideos(source?: VideoSource | null): Promise<Video[]> {
  // /api/catalog/titles defaults to watchable-only (ready episodes).
  const q = new URLSearchParams({ watchable: "1" })
  if (source) q.set("source", source)
  const { titles } = await getJson<{ titles: Title[] }>(`/api/catalog/titles?${q}`)
  const lists = await Promise.all(
    (titles || []).map(async (title) => {
      const { episodes } = await getJson<{ episodes: Episode[] }>(
        `/api/catalog/titles/${encodeURIComponent(title.id)}/episodes`
      )
      return (episodes || [])
        .filter((ep) => {
          const st = (ep.suggest_status || "").toLowerCase()
          const key = (ep.video_key || "").trim()
          if (!key || key.startsWith("movies/")) return false
          if (st === "processing" || st === "failed") return false
          return true
        })
        .map((ep) => ({ ...ep, title }))
    })
  )
  return lists.flat()
}

export async function getVideo(titleId: string, episodeId: string): Promise<Video | null> {
  const [{ titles }, { episodes }] = await Promise.all([
    getJson<{ titles: Title[] }>("/api/catalog/titles"),
    getJson<{ episodes: Episode[] }>(`/api/catalog/titles/${encodeURIComponent(titleId)}/episodes`),
  ])
  const title = titles.find((t) => t.id === titleId)
  const ep = episodes.find((e) => e.id === episodeId)
  return title && ep ? { ...ep, title } : null
}

export async function listMoments(episodeId: string, brandId?: string): Promise<CatalogMoment[]> {
  const q = new URLSearchParams({ episodeId })
  if (brandId) q.set("brandId", brandId)
  const { moments } = await getJson<{ moments: CatalogMoment[] }>(`/api/catalog/moments?${q}`)
  return moments || []
}

export async function listPlacements(brandId: string, episodeId?: string): Promise<Placement[]> {
  const q = new URLSearchParams({ brandId })
  if (episodeId) q.set("episodeId", episodeId)
  const { placements } = await getJson<{ placements: Placement[] }>(`/api/catalog/placements?${q}`)
  return placements || []
}

/** Approving queues the real placement-swap path when keys allow; otherwise holds queued. */
export async function decidePlacement(
  brandId: string,
  momentId: string,
  status: "approved" | "rejected"
): Promise<Placement> {
  return getJson<Placement>("/api/catalog/placements", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brandId, momentId, status, confirm: true }),
  })
}

export async function listCampaigns(brandId: string): Promise<Campaign[]> {
  const { campaigns } = await getJson<{ campaigns: Campaign[] }>(
    `/api/catalog/campaigns?brandId=${encodeURIComponent(brandId)}`
  )
  return campaigns || []
}

export async function saveCampaign(input: {
  brandId: string
  episodeId: string
  titleId?: string
  status?: Campaign["status"]
}): Promise<Campaign> {
  return getJson<Campaign>("/api/catalog/campaigns", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export async function getGenerationInfo(): Promise<GenerationInfo> {
  const data = await getJson<{ generation?: GenerationInfo }>("/api/health")
  return (
    data.generation || {
      ready: false,
      mock: false,
      keysConfigured: false,
      reason: "Generation status unavailable",
    }
  )
}

export async function createBrand(name: string, brief: string, asset?: File | null) {
  const form = new FormData()
  form.set("name", name)
  form.set("brief", brief)
  if (asset) form.set("asset", asset)
  return getJson<{ id: string; name: string }>("/api/catalog/brands", { method: "POST", body: form })
}

/* ---------- session (per browser) ---------- */

export type Session = { brandId: string; profile: BrandProfile; hasAsset?: boolean }
export type Draft = { titleId: string; episodeId: string; savedAt: string }

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function write(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage blocked */
  }
}

export const session = {
  get: () => read<Session | null>("inset.session", null),
  set: (s: Session) => write("inset.session", s),
  drafts: () => read<Draft[]>("inset.drafts", []),
  saveDraft: (d: Draft) =>
    write("inset.drafts", [d, ...read<Draft[]>("inset.drafts", []).filter((x) => x.episodeId !== d.episodeId)]),
  removeDraft: (episodeId: string) =>
    write("inset.drafts", read<Draft[]>("inset.drafts", []).filter((x) => x.episodeId !== episodeId)),
}

export function fmt(s: number, withMs = true): string {
  s = Math.max(0, s || 0)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = withMs ? (s % 60).toFixed(2).padStart(5, "0") : String(Math.floor(s % 60)).padStart(2, "0")
  return (h ? `${h}:${String(m).padStart(2, "0")}` : String(m).padStart(2, "0")) + ":" + sec
}

/** Moment region is in source pixels; convert to percentages of the frame. */
export function regionPct(m: CatalogMoment) {
  const r = m.region
  const W = m.frame_w || 1280
  const H = m.frame_h || 720
  if (!r) return null
  const pct = (n: number) => `${n * 100}%`
  return { left: pct(r.x / W), top: pct(r.y / H), width: pct(r.w / W), height: pct(r.h / H) }
}
