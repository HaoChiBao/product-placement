"use client"

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useEffect, useState } from "react"

import {
  PlacementPlayer,
  type PlacementWindow,
} from "@/components/placement-player"
import { WatchChrome } from "@/components/watch-chrome"

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
  const [status, setStatus] = useState<number | null>(null)

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
        setStatus(res.status)
        if (!res.ok) throw new Error(json.error || "failed")
        setData(json)
      })
      .catch((e) => setError(String(e.message || e)))
  }, [slug, season, episode, brandId])

  const missing = status === 404

  return (
    <>
      <WatchChrome
        current="play"
        meta={
          data
            ? `${data.title.name} · S${data.episode.season}E${data.episode.episode_number}`
            : undefined
        }
      />
      <main className="watch-main">
        <div className="watch-play">
          <nav className="watch-crumb" aria-label="Breadcrumb">
            <Link href="/watch">Catalog</Link>
            <span className="watch-crumb-sep" aria-hidden>
              /
            </span>
            <Link href={`/watch/${slug}`}>{data?.title.name || slug}</Link>
            <span className="watch-crumb-sep" aria-hidden>
              /
            </span>
            <span>
              S{season}E{episode}
            </span>
          </nav>

          {error && missing ? (
            <div className="watch-empty">
              <h1>Episode not found</h1>
              <p style={{ margin: 0 }}>{error}</p>
              <div className="watch-hero-actions">
                <Link href={`/watch/${slug}`} className="watch-btn watch-btn-ghost">
                  Title page
                </Link>
                <Link href="/watch" className="watch-btn watch-btn-primary">
                  Back to catalog
                </Link>
              </div>
            </div>
          ) : null}

          {error && !missing ? (
            <p className="watch-error" role="alert">
              {error}
            </p>
          ) : null}

          {!error && !data ? (
            <div aria-busy="true">
              <div
                className="watch-skel"
                style={{ width: "100%", aspectRatio: "16 / 9", maxWidth: "100%" }}
              />
              <p className="watch-tile-meta" style={{ marginTop: 12 }}>
                Loading episode…
              </p>
            </div>
          ) : null}

          {data && !data.video_url ? (
            <div className="watch-empty">
              <h2>Video unavailable</h2>
              <p style={{ margin: 0 }}>
                This episode has no playable original yet.
              </p>
              <Link href="/watch" className="watch-btn watch-btn-primary">
                Back to catalog
              </Link>
            </div>
          ) : null}

          {data && data.video_url ? (
            <>
              <PlacementPlayer
                videoUrl={data.video_url}
                posterUrl={data.poster_url}
                placements={(data.placements || []).filter((p) => p.clip_url)}
              />
              <div className="watch-play-meta">
                <h1>
                  {data.title.name}
                  <span>
                    S{data.episode.season}E{data.episode.episode_number} ·{" "}
                    {data.episode.name}
                  </span>
                </h1>
                {data.title.attribution ? (
                  <p className="watch-attr">{data.title.attribution}</p>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </main>
    </>
  )
}
