"use client"

import Link from "next/link"
import { useEffect, useState } from "react"

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

export default function WatchHomePage() {
  const [titles, setTitles] = useState<Title[]>([])
  const [episodes, setEpisodes] = useState<Record<string, Episode[]>>({})
  const [error, setError] = useState("")
  const [seeding, setSeeding] = useState(false)

  async function load() {
    const res = await fetch("/api/catalog/titles")
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || "failed")
    setTitles(data.titles || [])
    const map: Record<string, Episode[]> = {}
    await Promise.all(
      (data.titles || []).map(async (t: Title) => {
        const er = await fetch(`/api/catalog/titles/${t.id}/episodes`)
        const ed = await er.json()
        map[t.id] = ed.episodes || []
      })
    )
    setEpisodes(map)
  }

  useEffect(() => {
    load().catch((e) => setError(String(e.message || e)))
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

  return (
    <div className="min-h-screen bg-[#0b0b0d] text-zinc-100">
      <SiteNav />
      <div className="px-5 pb-16 pt-2">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-[family-name:var(--font-display)] text-3xl tracking-tight text-white md:text-4xl">
              Watch
            </h1>
            <p className="mt-2 max-w-xl text-sm text-zinc-400">
              Open-movie catalog with brand placements overlaid in sync. Attribution
              stays with each title.
            </p>
          </div>
          {titles.length === 0 ? (
            <button
              type="button"
              onClick={seed}
              disabled={seeding}
              className="rounded-md bg-white px-4 py-2 text-sm font-medium text-black disabled:opacity-50"
            >
              {seeding ? "Seeding…" : "Seed Blender catalog"}
            </button>
          ) : null}
        </div>

        {error ? (
          <p className="mb-4 whitespace-pre-wrap text-sm text-red-300">{error}</p>
        ) : null}

        {titles.map((t) => (
          <section key={t.id} className="mb-10">
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h2 className="text-lg font-medium text-white">{t.name}</h2>
              <span className="text-xs uppercase tracking-wide text-zinc-500">
                {t.kind}
              </span>
            </div>
            {t.attribution ? (
              <p className="mb-3 text-xs text-zinc-500">{t.attribution}</p>
            ) : null}
            <div className="flex gap-3 overflow-x-auto pb-2">
              {(episodes[t.id] || []).map((ep) => {
                const href = `/watch/${t.slug}/${ep.season}-${ep.episode_number}`
                const poster =
                  mediaUrl(ep.poster_key) || mediaUrl(t.poster_key) || undefined
                return (
                  <Link
                    key={ep.id}
                    href={href}
                    className="group w-[200px] shrink-0"
                  >
                    <div className="aspect-video overflow-hidden rounded-md bg-zinc-900 ring-1 ring-white/10 transition group-hover:ring-white/40">
                      {poster ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={poster}
                          alt=""
                          className="h-full w-full object-cover transition duration-300 group-hover:scale-[1.03]"
                        />
                      ) : (
                        <div className="flex h-full items-center justify-center text-xs text-zinc-600">
                          No poster
                        </div>
                      )}
                    </div>
                    <div className="mt-2 text-sm text-zinc-200">{ep.name}</div>
                    <div className="text-xs text-zinc-500">
                      S{ep.season}E{ep.episode_number}
                      {ep.duration_s
                        ? ` · ${Math.round(ep.duration_s)}s`
                        : ""}
                    </div>
                  </Link>
                )
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
