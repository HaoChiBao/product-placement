"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"

import { AnimatedGroup } from "@/components/motion-primitives/animated-group"
import { InView } from "@/components/motion-primitives/in-view"
import { TextEffect } from "@/components/motion-primitives/text-effect"
import { Button, ButtonLink } from "@/components/ui/button"
import { EmptyState, Skeleton } from "@/components/ui/empty-state"
import { WatchChrome } from "@/components/watch-chrome"
import type { Episode, Title } from "@/lib/catalog-client"
import {
  formatRuntime,
  loadWatchCatalog,
  playHref,
  posterFor,
  titleHref,
} from "@/lib/watch"
import { cn } from "@/lib/utils"

export default function WatchHomePage() {
  const [titles, setTitles] = useState<Title[]>([])
  const [episodes, setEpisodes] = useState<Record<string, Episode[]>>({})
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [seeding, setSeeding] = useState(false)

  async function load() {
    const data = await loadWatchCatalog()
    setTitles(data.titles)
    setEpisodes(data.episodes)
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
  const heroPoster = hero ? posterFor(hero, heroEp) : null

  return (
    <>
      <WatchChrome current="watch" />
      <main className="pb-[72px]">
        {loading ? (
          <>
            <div
              className="mb-7 min-h-[min(52vh,420px)] w-full animate-shimmer bg-[linear-gradient(90deg,var(--card),var(--muted),var(--card))] bg-[length:200%_100%]"
              aria-hidden
            />
            <section
              className="mb-9 px-4 sm:px-6 lg:px-10"
              aria-busy="true"
              aria-label="Loading catalog"
            >
              <div className="mb-3.5 flex items-baseline justify-between gap-3">
                <h2>Movies</h2>
              </div>
              <div className="flex gap-3.5 overflow-hidden">
                {[0, 1, 2, 3, 4].map((i) => (
                  <Skeleton
                    key={i}
                    className="aspect-[2/3] h-auto w-[148px] shrink-0 rounded-md"
                  />
                ))}
              </div>
            </section>
          </>
        ) : null}

        {!loading && hero && heroEp ? (
          <section
            className="relative mb-9 grid min-h-[min(72vh,640px)] items-end overflow-hidden max-[720px]:min-h-[68vh]"
            aria-label="Featured"
          >
            <div className="absolute inset-0 bg-card">
              {heroPoster ? (
                <img
                  src={heroPoster}
                  alt=""
                  className="block h-full w-full scale-[1.02] object-cover"
                />
              ) : (
                <div className="watch-poster-ph absolute inset-0" />
              )}
              <div
                className="absolute inset-0"
                style={{
                  background:
                    "linear-gradient(90deg, rgba(7, 8, 9, 0.92) 0%, rgba(7, 8, 9, 0.55) 42%, rgba(7, 8, 9, 0.2) 70%, transparent 100%), linear-gradient(0deg, var(--background) 0%, transparent 42%)",
                }}
              />
            </div>
            <div className="relative z-[1] flex max-w-[640px] flex-col gap-3.5 px-4 pb-10 pt-[clamp(28px,6vh,64px)] sm:px-6 lg:px-10">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
                {hero.kind === "series" ? "Series" : "Featured film"}
              </p>
              <TextEffect
                as="h1"
                preset="fade-in-blur"
                per="word"
                className="font-display text-[clamp(36px,6vw,64px)] font-bold leading-[0.98] tracking-[-0.035em] text-white"
              >
                {hero.name}
              </TextEffect>
              {hero.synopsis ? (
                <p className="m-0 max-w-[48ch] text-[15px] leading-[1.55] text-muted-foreground">
                  {hero.synopsis}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <ButtonLink href={playHref(hero, heroEp)} variant="cinema">
                  {hero.kind === "series"
                    ? `Play S${heroEp.season}E${heroEp.episode_number}`
                    : "Play"}
                </ButtonLink>
                <ButtonLink href={titleHref(hero)} variant="cinema-ghost">
                  Details
                </ButtonLink>
                {hero.attribution ? (
                  <span className="text-xs text-faint">{hero.attribution}</span>
                ) : null}
              </div>
            </div>
          </section>
        ) : null}

        {!loading && !hero ? (
          <EmptyState
            className="mx-4 my-12 border-[1.5px] bg-white/[0.02] sm:mx-6 lg:mx-10"
            title="Nothing to watch yet"
            description="Titles with ready episodes will appear here. Seed the Blender open-movie set, or import media into the catalog."
            action={
              <Button variant="cinema" onClick={seed} disabled={seeding}>
                {seeding ? "Adding open movies…" : "Add Blender open movies"}
              </Button>
            }
          />
        ) : null}

        {error ? (
          <p className="mx-4 mb-4 text-sm text-destructive sm:mx-6 lg:mx-10" role="alert">
            {error}
          </p>
        ) : null}

        {!loading && titles.length > 0 ? (
          <div className={cn("mb-9 px-4 sm:px-6 lg:px-10", !hero && "mt-6")}>
            <Button variant="cinema-ghost" onClick={seed} disabled={seeding}>
              {seeding ? "Adding…" : "Add Blender open movies"}
            </Button>
          </div>
        ) : null}

        {!loading ? (
          <>
            <Rail heading="Movies" titles={movies} episodes={episodes} mode="movie" />
            <Rail heading="Shows" titles={shows} episodes={episodes} mode="series" />
          </>
        ) : null}
      </main>
    </>
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
    <InView
      once
      viewOptions={{ margin: "-80px" }}
      variants={{
        hidden: { opacity: 0, y: 16 },
        visible: { opacity: 1, y: 0 },
      }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
    >
      <section
        className="mb-9 px-4 sm:px-6 lg:px-10"
        aria-labelledby={`rail-${mode}`}
      >
        <div className="mb-3.5 flex items-baseline justify-between gap-3">
          <h2 id={`rail-${mode}`}>{heading}</h2>
        </div>
        <AnimatedGroup
          preset="blur-slide"
          className="flex snap-x snap-mandatory gap-3.5 overflow-x-auto scroll-smooth pb-2 [-webkit-overflow-scrolling:touch] [scrollbar-width:thin]"
          variants={{
            container: {
              hidden: { opacity: 0 },
              visible: {
                opacity: 1,
                transition: { staggerChildren: 0.04 },
              },
            },
            item: {
              hidden: { opacity: 0, y: 10, filter: "blur(4px)" },
              visible: {
                opacity: 1,
                y: 0,
                filter: "blur(0px)",
                transition: { duration: 0.28, ease: [0.22, 1, 0.36, 1] },
              },
            },
          }}
        >
          {titles.map((t) => {
            const eps = episodes[t.id] || []
            if (mode === "movie") {
              const ep = eps[0]
              const poster = posterFor(t, ep)
              return (
                <Link
                  key={t.id}
                  href={titleHref(t)}
                  className="w-[148px] shrink-0 snap-start rounded-md text-inherit max-[720px]:w-[120px] [&:hover_.watch-poster]:-translate-y-0.5 [&:hover_.watch-poster]:outline [&:hover_.watch-poster]:outline-2 [&:hover_.watch-poster]:outline-offset-[3px] [&:hover_.watch-poster]:outline-accent [&:focus-visible_.watch-poster]:-translate-y-0.5 [&:focus-visible_.watch-poster]:outline [&:focus-visible_.watch-poster]:outline-2 [&:focus-visible_.watch-poster]:outline-offset-[3px] [&:focus-visible_.watch-poster]:outline-accent"
                >
                  <div className="watch-poster">
                    {poster ? <img src={poster} alt="" /> : <div className="watch-poster-ph">No poster</div>}
                  </div>
                  <div className="mt-2.5 line-clamp-2 text-[13.5px] font-semibold leading-snug">
                    {t.name}
                  </div>
                  <div className="mt-0.5 text-xs text-faint">
                    {formatRuntime(ep?.duration_s) || "Movie"}
                  </div>
                </Link>
              )
            }
            return (
              <div
                key={t.id}
                className="w-[min(100%,420px)] shrink-0 snap-start"
              >
                <Link
                  href={titleHref(t)}
                  className="mb-2.5 block text-sm font-semibold"
                >
                  {t.name}
                </Link>
                <div className="flex gap-2.5 overflow-x-auto pb-1">
                  {eps.map((ep) => {
                    const src = posterFor(t, ep)
                    return (
                      <Link
                        key={ep.id}
                        href={playHref(t, ep)}
                        className="w-[168px] shrink-0 text-inherit max-[720px]:w-[148px] [&:hover_.watch-poster]:outline [&:hover_.watch-poster]:outline-2 [&:hover_.watch-poster]:outline-offset-[3px] [&:hover_.watch-poster]:outline-accent [&:focus-visible_.watch-poster]:outline [&:focus-visible_.watch-poster]:outline-2 [&:focus-visible_.watch-poster]:outline-offset-[3px] [&:focus-visible_.watch-poster]:outline-accent"
                      >
                        <div className="watch-poster watch-poster-land">
                          {src ? <img src={src} alt="" /> : <div className="watch-poster-ph">No poster</div>}
                        </div>
                        <div className="mt-2.5 line-clamp-2 text-[13.5px] font-semibold leading-snug">
                          S{ep.season}E{ep.episode_number}
                        </div>
                        <div className="mt-0.5 text-xs text-faint">{ep.name}</div>
                      </Link>
                    )
                  })}
                  {eps.length === 0 ? (
                    <p className="text-xs text-faint">No episodes yet</p>
                  ) : null}
                </div>
              </div>
            )
          })}
        </AnimatedGroup>
      </section>
    </InView>
  )
}
