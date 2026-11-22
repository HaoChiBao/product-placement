"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"

import { WatchChrome } from "@/components/watch-chrome"
import type { Episode, Title } from "@/lib/catalog-client"
import {
  formatRuntime,
  loadWatchCatalog,
  playHref,
  posterFor,
  titleHref,
} from "@/lib/watch"

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
      <main className="watch-main">
        {loading ? (
          <>
            <div className="watch-skel watch-skel-wide" aria-hidden />
            <section className="watch-section" aria-busy="true" aria-label="Loading catalog">
              <div className="watch-section-h">
                <h2>Movies</h2>
              </div>
              <div className="watch-skel-rail">
                {[0, 1, 2, 3, 4].map((i) => (
                  <div key={i} className="watch-skel" />
                ))}
              </div>
            </section>
          </>
        ) : null}

        {!loading && hero && heroEp ? (
          <section className="watch-hero" aria-label="Featured">
            <div className="watch-hero-media">
              {heroPoster ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={heroPoster} alt="" />
              ) : (
                <div className="watch-poster-ph" style={{ position: "absolute", inset: 0 }} />
              )}
            </div>
            <div className="watch-hero-body">
              <p className="watch-kicker">
                {hero.kind === "series" ? "Series" : "Featured film"}
              </p>
              <h1>{hero.name}</h1>
              {hero.synopsis ? <p>{hero.synopsis}</p> : null}
              <div className="watch-hero-actions">
                <Link href={playHref(hero, heroEp)} className="watch-btn watch-btn-primary">
                  {hero.kind === "series"
                    ? `Play S${heroEp.season}E${heroEp.episode_number}`
                    : "Play"}
                </Link>
                <Link href={titleHref(hero)} className="watch-btn watch-btn-ghost">
                  Details
                </Link>
                {hero.attribution ? (
                  <span className="watch-attr">{hero.attribution}</span>
                ) : null}
              </div>
            </div>
          </section>
        ) : null}

        {!loading && !hero ? (
          <div className="watch-empty">
            <h1>Nothing to watch yet</h1>
            <p style={{ maxWidth: "42ch", margin: 0 }}>
              Titles with ready episodes will appear here. Seed the Blender open-movie
              set, or import media into the catalog.
            </p>
            <button
              type="button"
              className="watch-btn watch-btn-primary"
              onClick={seed}
              disabled={seeding}
            >
              {seeding ? "Adding open movies…" : "Add Blender open movies"}
            </button>
          </div>
        ) : null}

        {error ? <p className="watch-error" role="alert">{error}</p> : null}

        {!loading && titles.length > 0 ? (
          <div className="watch-section" style={{ marginTop: hero ? 0 : 24 }}>
            <button
              type="button"
              className="watch-btn watch-btn-ghost"
              onClick={seed}
              disabled={seeding}
            >
              {seeding ? "Adding…" : "Add Blender open movies"}
            </button>
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
    <section className="watch-section" aria-labelledby={`rail-${mode}`}>
      <div className="watch-section-h">
        <h2 id={`rail-${mode}`}>{heading}</h2>
      </div>
      <div className="watch-rail">
        {titles.map((t) => {
          const eps = episodes[t.id] || []
          if (mode === "movie") {
            const ep = eps[0]
            const poster = posterFor(t, ep)
            return (
              <Link key={t.id} href={titleHref(t)} className="watch-tile">
                <div className="watch-poster">
                  {poster ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={poster} alt="" />
                  ) : (
                    <div className="watch-poster-ph">No poster</div>
                  )}
                </div>
                <div className="watch-tile-title">{t.name}</div>
                <div className="watch-tile-meta">
                  {formatRuntime(ep?.duration_s) || "Movie"}
                </div>
              </Link>
            )
          }
          return (
            <div key={t.id} className="watch-show">
              <Link href={titleHref(t)} className="watch-show-name">
                {t.name}
              </Link>
              <div className="watch-show-eps">
                {eps.map((ep) => {
                  const src = posterFor(t, ep)
                  return (
                    <Link key={ep.id} href={playHref(t, ep)} className="watch-ep">
                      <div className="watch-poster watch-poster-land">
                        {src ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={src} alt="" />
                        ) : (
                          <div className="watch-poster-ph">No poster</div>
                        )}
                      </div>
                      <div className="watch-tile-title">
                        S{ep.season}E{ep.episode_number}
                      </div>
                      <div className="watch-tile-meta">{ep.name}</div>
                    </Link>
                  )
                })}
                {eps.length === 0 ? (
                  <p className="watch-tile-meta">No episodes yet</p>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
