"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"

import { SiteNav } from "@/components/site-nav"

type Title = {
  id: string
  name: string
  slug: string
  kind: string
  synopsis?: string
  poster_key?: string | null
  attribution?: string
}

type Episode = {
  id: string
  season: number
  episode_number: number
  name: string
  poster_key?: string | null
  duration_s?: number
}

function mediaUrl(key?: string | null) {
  if (!key) return null
  return `/api/media/${key.split("/").map(encodeURIComponent).join("/")}`
}

function playHref(title: Title, ep: Episode) {
  return `/watch/${title.slug}/${ep.season}-${ep.episode_number}`
}

function formatRuntime(seconds?: number) {
  if (!seconds || seconds <= 0) return null
  const m = Math.round(seconds / 60)
  if (m < 1) return `${Math.round(seconds)}s`
  return `${m} min`
}

export default function WatchHomePage() {
  const [titles, setTitles] = useState<Title[]>([])
  const [episodes, setEpisodes] = useState<Record<string, Episode[]>>({})
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [seeding, setSeeding] = useState(false)

  async function load() {
    const res = await fetch("/api/catalog/titles")
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || "failed")
    const list = (data.titles || []) as Title[]
    setTitles(list)
    const map: Record<string, Episode[]> = {}
    await Promise.all(
      list.map(async (t) => {
        const er = await fetch(`/api/catalog/titles/${t.id}/episodes`)
        const ed = await er.json()
        map[t.id] = ed.episodes || []
      })
    )
    setEpisodes(map)
  }

  useEffect(() => {
    load()
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false))
  }, [])

  async function seed() {
    setSeeding(true)
    setError("")
    try {
      const res = await fetch("/api/catalog/seed", { method: "POST" })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || "seed failed")
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSeeding(false)
    }
  }

  const movies = useMemo(
    () => titles.filter((t) => t.kind !== "series"),
    [titles]
  )
  const shows = useMemo(
    () => titles.filter((t) => t.kind === "series"),
    [titles]
  )
  const hero = movies[0] || shows[0] || null
  const heroEp = hero ? episodes[hero.id]?.[0] : undefined
  const heroPoster = hero
    ? mediaUrl(hero.poster_key) || mediaUrl(heroEp?.poster_key)
    : null

  return (
    <div className="min-h-screen bg-[#0b0b0d] text-zinc-100">
      <SiteNav />
      <div className="px-5 pb-16 pt-2">
        {hero && heroEp ? (
          <section className="relative mb-10 overflow-hidden rounded-2xl bg-zinc-900">
            <div className="grid min-h-[280px] md:grid-cols-[1.4fr_1fr]">
              <div className="flex flex-col justify-end gap-3 p-6 md:p-10">
                <p className="text-xs uppercase tracking-[0.2em] text-zinc-400">
                  {hero.kind === "series" ? "Show" : "Movie"}
                </p>
                <h1 className="font-[family-name:var(--font-display)] text-4xl tracking-tight text-white md:text-5xl">
                  {hero.name}
                </h1>
                {hero.synopsis ? (
                  <p className="max-w-xl text-sm leading-relaxed text-zinc-300">
                    {hero.synopsis}
                  </p>
                ) : null}
                <div className="flex flex-wrap items-center gap-3 pt-1">
                  <Link
                    href={playHref(hero, heroEp)}
                    className="rounded-md bg-white px-4 py-2 text-sm font-medium text-black"
                  >
                    {hero.kind === "series"
                      ? `Play S${heroEp.season}E${heroEp.episode_number}`
                      : "Play"}
                  </Link>
                  {hero.attribution ? (
                    <span className="text-xs text-zinc-500">{hero.attribution}</span>
                  ) : null}
                </div>
              </div>
              <div className="relative min-h-[180px] bg-zinc-800">
                {heroPoster ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={heroPoster}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                ) : null}
              </div>
            </div>
          </section>
        ) : (
          <header className="mb-8">
            <h1 className="font-[family-name:var(--font-display)] text-3xl tracking-tight text-white md:text-4xl">
              Watch
            </h1>
            <p className="mt-2 max-w-xl text-sm text-zinc-400">
              Movies and shows live in the catalog before anyone opens the app.
              Drop a file at{" "}
              <span className="font-mono text-zinc-300">
                catalog/&lt;slug&gt;/s01e01/video.mp4
              </span>{" "}
              (poster next to it), then register the title. A movie is one
              episode; a show is a season of them.
            </p>
          </header>
        )}

        <div className="mb-8 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={seed}
            disabled={seeding}
            className="rounded-md border border-white/15 px-4 py-2 text-sm text-zinc-200 disabled:opacity-50"
          >
            {seeding ? "Adding open movies…" : "Add Blender open movies"}
          </button>
          {loading ? (
            <span className="text-xs text-zinc-500">Loading catalog…</span>
          ) : null}
        </div>

        {error ? (
          <p className="mb-4 whitespace-pre-wrap text-sm text-red-300">{error}</p>
        ) : null}

        {!loading && titles.length === 0 ? (
          <p className="text-sm text-zinc-500">
            Nothing is registered yet. Preload objects under{" "}
            <span className="font-mono">catalog/&lt;slug&gt;/</span>, or add the
            Blender open-movie set.
          </p>
        ) : null}

        <Rail
          heading="Movies"
          titles={movies}
          episodes={episodes}
          mode="movie"
        />
        <Rail
          heading="Shows"
          titles={shows}
          episodes={episodes}
          mode="series"
        />
      </div>
    </div>
  )
}

function Rail({
  heading,
  titles,
  episodes,
  mode,
}: {
  heading: string
  titles: Title[]
  episodes: Record<string, Episode[]>
  mode: "movie" | "series"
}) {
  if (!titles.length) return null
  return (
    <section className="mb-10">
      <h2 className="mb-3 text-lg font-medium text-white">{heading}</h2>
      <div className="flex gap-4 overflow-x-auto pb-2">
        {titles.map((t) => {
          const eps = episodes[t.id] || []
          if (mode === "movie") {
            const ep = eps[0]
            const poster = mediaUrl(t.poster_key) || mediaUrl(ep?.poster_key)
            const href = ep ? playHref(t, ep) : "/watch"
            return (
              <Link key={t.id} href={href} className="group w-[220px] shrink-0">
                <Poster src={poster} />
                <div className="mt-2 text-sm text-zinc-100">{t.name}</div>
                <div className="text-xs text-zinc-500">
                  {formatRuntime(ep?.duration_s) || "Movie"}
                </div>
              </Link>
            )
          }
          return (
            <div key={t.id} className="w-[280px] shrink-0">
              <div className="mb-2 text-sm text-zinc-100">{t.name}</div>
              <div className="flex gap-2 overflow-x-auto">
                {eps.map((ep) => (
                  <Link
                    key={ep.id}
                    href={playHref(t, ep)}
                    className="w-[140px] shrink-0"
                  >
                    <Poster
                      src={mediaUrl(ep.poster_key) || mediaUrl(t.poster_key)}
                    />
                    <div className="mt-1 text-xs text-zinc-300">
                      S{ep.season}E{ep.episode_number}
                    </div>
                    <div className="truncate text-xs text-zinc-500">{ep.name}</div>
                  </Link>
                ))}
                {eps.length === 0 ? (
                  <p className="text-xs text-zinc-500">No episodes yet</p>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function Poster({ src }: { src: string | null }) {
  return (
    <div className="aspect-video overflow-hidden rounded-md bg-zinc-900 ring-1 ring-white/10">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full items-center justify-center text-xs text-zinc-600">
          No poster
        </div>
      )}
    </div>
  )
}
