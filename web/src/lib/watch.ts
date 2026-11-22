import type { Episode, Title } from "@/lib/catalog-client"
import { mediaUrl } from "@/lib/catalog-client"

export type WatchTitle = Title
export type WatchEpisode = Episode

export function isListableEpisode(ep: Pick<Episode, "suggest_status" | "video_key">) {
  const status = (ep.suggest_status || "").toLowerCase()
  if (status === "processing" || status === "failed") return false
  return Boolean(ep.video_key && String(ep.video_key).trim())
}

export function playHref(title: Pick<Title, "slug">, ep: Pick<Episode, "season" | "episode_number">) {
  return `/watch/${title.slug}/${ep.season}-${ep.episode_number}`
}

export function titleHref(title: Pick<Title, "slug">) {
  return `/watch/${title.slug}`
}

export function formatRuntime(seconds?: number | null) {
  if (!seconds || seconds <= 0) return null
  const m = Math.round(seconds / 60)
  if (m < 1) return `${Math.round(seconds)}s`
  return `${m} min`
}

export function posterFor(title: Title, ep?: Episode | null) {
  return mediaUrl(title.poster_key) || mediaUrl(ep?.poster_key) || null
}

export async function loadWatchCatalog(): Promise<{
  titles: Title[]
  episodes: Record<string, Episode[]>
}> {
  const res = await fetch("/api/catalog/titles?watchable=1")
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || "failed to load catalog")
  const list = (data.titles || []) as Title[]
  const map: Record<string, Episode[]> = {}
  await Promise.all(
    list.map(async (t) => {
      const er = await fetch(`/api/catalog/titles/${encodeURIComponent(t.id)}/episodes`)
      const ed = await er.json()
      map[t.id] = ((ed.episodes || []) as Episode[]).filter(isListableEpisode)
    })
  )
  const titles = list.filter((t) => (map[t.id] || []).length > 0)
  return { titles, episodes: map }
}
