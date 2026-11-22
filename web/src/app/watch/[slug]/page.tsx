"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useEffect, useState } from "react"

import { AnimatedGroup } from "@/components/motion-primitives/animated-group"
import { InView } from "@/components/motion-primitives/in-view"
import { TextEffect } from "@/components/motion-primitives/text-effect"
import { ButtonLink } from "@/components/ui/button"
import { EmptyState, Skeleton } from "@/components/ui/empty-state"
import { WatchChrome } from "@/components/watch-chrome"
import type { Episode, Title } from "@/lib/catalog-client"
import {
  formatRuntime,
  isListableEpisode,
  playHref,
  posterFor,
} from "@/lib/watch"

export default function WatchTitlePage() {
  const params = useParams<{ slug: string }>()
  const slug = String(params.slug || "")
  const [title, setTitle] = useState<Title | null>(null)
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function run() {
      setLoading(true)
      setError("")
      setNotFound(false)
      try {
        const res = await fetch("/api/catalog/titles?watchable=1")
        const data = await res.json()
        if (!res.ok) throw new Error(data.error || "failed")
        const found = ((data.titles || []) as Title[]).find((t) => t.slug === slug)
        if (!found) {
          if (!cancelled) {
            setNotFound(true)
            setTitle(null)
            setEpisodes([])
          }
          return
        }
        const er = await fetch(
          `/api/catalog/titles/${encodeURIComponent(found.id)}/episodes`
        )
        const ed = await er.json()
        if (!er.ok) throw new Error(ed.error || "failed to load episodes")
        const eps = ((ed.episodes || []) as Episode[]).filter(isListableEpisode)
        if (!cancelled) {
          setTitle(found)
          setEpisodes(eps)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    run()
    return () => {
      cancelled = true
    }
  }, [slug])

  const first = episodes[0]
  const poster = title ? posterFor(title, first) : null

  return (
    <>
      <WatchChrome current="watch" />
      <main className="pb-[72px]">
        <div className="px-3 pb-0 pt-3 sm:px-6 lg:px-8">
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
            <span>{title?.name || slug}</span>
          </nav>
        </div>

        {loading ? (
          <div
            className="mb-10 grid grid-cols-[minmax(140px,220px)_minmax(0,1fr)] items-start gap-x-[clamp(20px,4vw,40px)] px-4 pt-5 sm:px-6 lg:px-10 max-[720px]:grid-cols-[120px_1fr] max-[720px]:gap-4"
            aria-busy="true"
          >
            <Skeleton className="aspect-[2/3] h-auto w-full rounded-md" />
            <div className="flex flex-col gap-3">
              <Skeleton className="h-9 w-3/5" />
              <Skeleton className="h-3.5 w-[90%]" />
              <Skeleton className="h-3.5 w-[70%]" />
            </div>
          </div>
        ) : null}

        {error ? (
          <p className="mx-4 mb-4 text-sm text-destructive sm:mx-6 lg:mx-10" role="alert">
            {error}
          </p>
        ) : null}

        {!loading && notFound ? (
          <EmptyState
            className="mx-4 my-12 border-[1.5px] bg-white/[0.02] sm:mx-6 lg:mx-10"
            title="Title not found"
            description={`No watchable title matches “${slug}”.`}
            action={
              <ButtonLink href="/watch" variant="cinema">
                Back to catalog
              </ButtonLink>
            }
          />
        ) : null}

        {!loading && title ? (
          <>
            <InView
              once
              variants={{
                hidden: { opacity: 0, y: 12 },
                visible: { opacity: 1, y: 0 },
              }}
              transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            >
              <section className="mb-10 grid grid-cols-[minmax(140px,220px)_minmax(0,1fr)] items-start gap-x-[clamp(20px,4vw,40px)] px-4 pt-5 sm:px-6 lg:px-10 max-[720px]:grid-cols-[120px_1fr] max-[720px]:gap-4">
                <div className="aspect-[2/3] overflow-hidden rounded-md border border-border bg-card shadow-[0_24px_60px_rgba(0,0,0,0.45)]">
                  {poster ? (
                    <img src={poster} alt="" className="block h-full w-full object-cover" />
                  ) : (
                    <div className="watch-poster-ph">No poster</div>
                  )}
                </div>
                <div className="flex flex-col gap-3.5 pt-2">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
                    {title.kind === "series" ? "Series" : "Movie"}
                  </p>
                  <TextEffect
                    as="h1"
                    preset="fade-in-blur"
                    per="word"
                    className="font-display text-[clamp(32px,5vw,52px)] font-bold leading-[1.02] tracking-[-0.03em] max-[720px]:text-[28px]"
                  >
                    {title.name}
                  </TextEffect>
                  {title.synopsis ? (
                    <p className="m-0 max-w-[58ch] text-[15px] leading-[1.55] text-muted-foreground">
                      {title.synopsis}
                    </p>
                  ) : null}
                  <div className="flex flex-wrap items-center gap-3 pt-1">
                    {first ? (
                      <ButtonLink href={playHref(title, first)} variant="cinema">
                        {title.kind === "series"
                          ? `Play S${first.season}E${first.episode_number}`
                          : "Play"}
                      </ButtonLink>
                    ) : (
                      <span className="text-xs text-faint">No playable episodes yet</span>
                    )}
                    {title.attribution ? (
                      <span className="text-xs text-faint">{title.attribution}</span>
                    ) : null}
                  </div>
                </div>
              </section>
            </InView>

            <InView
              once
              viewOptions={{ margin: "-60px" }}
              variants={{
                hidden: { opacity: 0, y: 14 },
                visible: { opacity: 1, y: 0 },
              }}
              transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            >
              <section className="mb-9" aria-labelledby="episodes-heading">
                <div className="mb-3.5 flex items-baseline justify-between gap-3 px-4 sm:px-6 lg:px-10">
                  <h2 id="episodes-heading">
                    {title.kind === "series" ? "Episodes" : "Play"}
                  </h2>
                </div>
                {episodes.length === 0 ? (
                  <p className="px-4 text-xs text-faint sm:px-6 lg:px-10">
                    Episodes are still processing or unavailable.
                  </p>
                ) : (
                  <AnimatedGroup
                    preset="blur-slide"
                    className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-x-3.5 gap-y-[18px] px-4 sm:px-6 lg:px-10"
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
                    {episodes.map((ep) => {
                      const src = posterFor(title, ep)
                      return (
                        <Link
                          key={ep.id}
                          href={playHref(title, ep)}
                          className="w-auto text-inherit [&:hover_.watch-poster]:outline [&:hover_.watch-poster]:outline-2 [&:hover_.watch-poster]:outline-offset-[3px] [&:hover_.watch-poster]:outline-accent [&:focus-visible_.watch-poster]:outline [&:focus-visible_.watch-poster]:outline-2 [&:focus-visible_.watch-poster]:outline-offset-[3px] [&:focus-visible_.watch-poster]:outline-accent"
                        >
                          <div className="watch-poster watch-poster-land">
                            {src ? <img src={src} alt="" /> : <div className="watch-poster-ph">No poster</div>}
                          </div>
                          <div className="mt-2.5 line-clamp-2 text-[13.5px] font-semibold leading-snug">
                            {title.kind === "series"
                              ? `S${ep.season}E${ep.episode_number} · ${ep.name}`
                              : ep.name}
                          </div>
                          <div className="mt-0.5 text-xs text-faint">
                            {formatRuntime(ep.duration_s) || "Ready"}
                          </div>
                        </Link>
                      )
                    })}
                  </AnimatedGroup>
                )}
              </section>
            </InView>
          </>
        ) : null}
      </main>
    </>
  )
}
