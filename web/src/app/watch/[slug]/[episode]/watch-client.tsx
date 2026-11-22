"use client"

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useEffect, useState } from "react"

import {
  PlacementPlayer,
  type PlacementWindow,
} from "@/components/placement-player"
import { SiteNav } from "@/components/site-nav"

type WatchPayload = {
  title: { name: string; slug: string; attribution?: string }
  episode: {
    name: string
    season: number
    episode_number: number
    duration_s?: number
  }
  video_url?: string | null
  poster_url?: string | null
  placements: PlacementWindow[]
}

export function WatchClient({
  slug,
  season,
  episode,
}: {
  slug: string
  season: number
  episode: number
}) {
  const search = useSearchParams()
  const brandId = search.get("brandId") || ""
  const [data, setData] = useState<WatchPayload | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    const qs = new URLSearchParams({
      slug,
      season: String(season),
      episode: String(episode),
    })
    if (brandId) qs.set("brandId", brandId)
    fetch(`/api/catalog/watch?${qs}`)
      .then(async (res) => {
        const json = await res.json()
        if (!res.ok) throw new Error(json.error || "failed")
        setData(json)
      })
      .catch((e) => setError(String(e.message || e)))
  }, [slug, season, episode, brandId])

  return (
    <div className="min-h-screen bg-black text-zinc-100">
      <SiteNav />
      <div className="px-3 pb-10 md:px-5">
        <div className="mb-3 flex items-center gap-3 text-sm text-zinc-400">
          <Link href="/watch" className="hover:text-white">
            ← Catalog
          </Link>
          {data ? (
            <span>
              {data.title.name} · S{data.episode.season}E
              {data.episode.episode_number}
            </span>
          ) : null}
        </div>

        {error ? (
          <p className="text-sm text-red-300">{error}</p>
        ) : !data ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : !data.video_url ? (
          <p className="text-sm text-zinc-500">No video for this episode.</p>
        ) : (
          <>
            <PlacementPlayer
              videoUrl={data.video_url}
              posterUrl={data.poster_url}
              placements={(data.placements || []).filter((p) => p.clip_url)}
              title={data.episode.name}
            />
            {data.title.attribution ? (
              <p className="mt-3 text-xs text-zinc-500">
                {data.title.attribution}
              </p>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
