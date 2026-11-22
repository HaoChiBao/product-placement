"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useEffect, useState } from "react"

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
      <main className="watch-main">
        <div className="watch-play" style={{ paddingBottom: 0 }}>
          <nav className="watch-crumb" aria-label="Breadcrumb">
            <Link href="/watch">Catalog</Link>
            <span className="watch-crumb-sep" aria-hidden>
              /
            </span>
            <span>{title?.name || slug}</span>
          </nav>
        </div>

        {loading ? (
          <div className="watch-title" aria-busy="true">
            <div className="watch-skel" style={{ width: "100%" }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="watch-skel" style={{ height: 36, width: "60%", aspectRatio: "auto" }} />
              <div className="watch-skel" style={{ height: 14, width: "90%", aspectRatio: "auto" }} />
              <div className="watch-skel" style={{ height: 14, width: "70%", aspectRatio: "auto" }} />
            </div>
          </div>
        ) : null}

        {error ? <p className="watch-error" role="alert">{error}</p> : null}

        {!loading && notFound ? (
          <div className="watch-empty">
            <h1>Title not found</h1>
            <p style={{ margin: 0 }}>No watchable title matches “{slug}”.</p>
            <Link href="/watch" className="watch-btn watch-btn-primary">
              Back to catalog
            </Link>
          </div>
        ) : null}

        {!loading && title ? (
          <>
            <section className="watch-title">
              <div className="watch-title-poster">
                {poster ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={poster} alt="" />
                ) : (
                  <div className="watch-poster-ph">No poster</div>
                )}
              </div>
              <div className="watch-title-body">
                <p className="watch-kicker">
                  {title.kind === "series" ? "Series" : "Movie"}
                </p>
                <h1>{title.name}</h1>
                {title.synopsis ? <p>{title.synopsis}</p> : null}
                <div className="watch-hero-actions">
                  {first ? (
                    <Link
                      href={playHref(title, first)}
                      className="watch-btn watch-btn-primary"
                    >
                      {title.kind === "series"
                        ? `Play S${first.season}E${first.episode_number}`
                        : "Play"}
                    </Link>
                  ) : (
                    <span className="watch-attr">No playable episodes yet</span>
                  )}
                  {title.attribution ? (
                    <span className="watch-attr">{title.attribution}</span>
                  ) : null}
                </div>
              </div>
            </section>

            <section className="watch-section" aria-labelledby="episodes-heading">
              <div className="watch-section-h">
                <h2 id="episodes-heading">
                  {title.kind === "series" ? "Episodes" : "Play"}
                </h2>
              </div>
              {episodes.length === 0 ? (
                <p className="watch-tile-meta">
                  Episodes are still processing or unavailable.
                </p>
              ) : (
                <div className="watch-ep-grid">
                  {episodes.map((ep) => {
                    const src = posterFor(title, ep)
                    return (
                      <Link
                        key={ep.id}
                        href={playHref(title, ep)}
                        className="watch-ep"
                        style={{ width: "auto" }}
                      >
                        <div className="watch-poster watch-poster-land">
                          {src ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={src} alt="" />
                          ) : (
                            <div className="watch-poster-ph">No poster</div>
                          )}
                        </div>
                        <div className="watch-tile-title">
                          {title.kind === "series"
                            ? `S${ep.season}E${ep.episode_number} · ${ep.name}`
                            : ep.name}
                        </div>
                        <div className="watch-tile-meta">
                          {formatRuntime(ep.duration_s) || "Ready"}
                        </div>
                      </Link>
                    )
                  })}
                </div>
              )}
            </section>
          </>
        ) : null}
      </main>
    </>
  )
}
