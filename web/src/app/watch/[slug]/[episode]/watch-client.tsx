"use client"

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useEffect, useState } from "react"

import {
  PlacementPlayer,
  type PlacementWindow,
} from "@/components/placement-player"
import { TextEffect } from "@/components/motion-primitives/text-effect"
import { ButtonLink } from "@/components/ui/button"
import { EmptyState, Skeleton } from "@/components/ui/empty-state"
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
      <main className="pb-[72px]">
        <div className="px-3 pb-10 pt-3 sm:px-6 lg:px-8">
          <nav
            className="mb-3 flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground"
            aria-label="Breadcrumb"
          >
            <Link href="/watch" className="hover:text-foreground">
              Catalog
            </Link>
            <span className="text-faint" aria-hidden>
              /
            </span>
            <Link href={`/watch/${slug}`} className="hover:text-foreground">
              {data?.title.name || slug}
            </Link>
            <span className="text-faint" aria-hidden>
              /
            </span>
            <span>
              S{season}E{episode}
            </span>
          </nav>

          {error && missing ? (
            <EmptyState
              className="my-12 border-[1.5px] bg-white/[0.02]"
              title="Episode not found"
              description={error}
              action={
                <div className="flex flex-wrap items-center gap-3">
                  <ButtonLink href={`/watch/${slug}`} variant="cinema-ghost">
                    Title page
                  </ButtonLink>
                  <ButtonLink href="/watch" variant="cinema">
                    Back to catalog
                  </ButtonLink>
                </div>
              }
            />
          ) : null}

          {error && !missing ? (
            <p className="mb-4 text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}

          {!error && !data ? (
            <div aria-busy="true">
              <Skeleton className="aspect-video h-auto w-full max-w-full rounded-md" />
              <p className="mt-3 text-xs text-faint">Loading episode…</p>
            </div>
          ) : null}

          {data && !data.video_url ? (
            <EmptyState
              className="my-12 border-[1.5px] bg-white/[0.02]"
              title="Video unavailable"
              description="This episode has no playable original yet."
              action={
                <ButtonLink href="/watch" variant="cinema">
                  Back to catalog
                </ButtonLink>
              }
            />
          ) : null}

          {data && data.video_url ? (
            <>
              <PlacementPlayer
                videoUrl={data.video_url}
                posterUrl={data.poster_url}
                durationS={data.episode.duration_s}
                placements={(data.placements || []).filter((p) => p.clip_url)}
              />
              <div className="flex flex-wrap items-baseline justify-between gap-2.5 px-1 pt-4">
                <h1 className="m-0 font-display text-lg tracking-tight">
                  <TextEffect
                    as="span"
                    preset="fade-in-blur"
                    per="word"
                    className="font-display text-lg font-bold tracking-tight"
                  >
                    {data.title.name}
                  </TextEffect>
                  <span className="ml-2 text-[13.5px] font-normal text-muted-foreground">
                    S{data.episode.season}E{data.episode.episode_number} · {data.episode.name}
                  </span>
                </h1>
                {data.title.attribution ? (
                  <p className="text-xs text-faint">{data.title.attribution}</p>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </main>
    </>
  )
}
