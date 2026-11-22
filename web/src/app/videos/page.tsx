"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

import { TopBar } from "@/components/top-bar"
import { VideoSourceToggle } from "@/components/video-source-toggle"
import { VideoThumb, videoLabel as label } from "@/components/video-thumb"
import {
  fmt,
  isScanned,
  listMoments,
  listVideos,
  mediaUrl,
  readVideoSource,
  regionPct,
  session,
  writeVideoSource,
  type CatalogMoment,
  type Video,
  type VideoSource,
} from "@/lib/catalog-client"

function initials(s: string) {
  return s.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("")
}

export default function VideosPage() {
  const router = useRouter()
  const [videos, setVideos] = useState<Video[] | null>(null)
  const [error, setError] = useState("")
  const [q, setQ] = useState("")
  const [open, setOpen] = useState<Video | null>(null)
  const [source, setSource] = useState<VideoSource>("all")

  function load(nextSource: VideoSource) {
    setVideos(null)
    setError("")
    listVideos(nextSource).then(setVideos).catch((e) => setError(String(e.message || e)))
  }

  useEffect(() => {
    if (!session.get()) {
      router.replace("/")
      return
    }
    const initial = readVideoSource() || "all"
    setSource(initial)
    load(initial)
  }, [router])

  function onSourceChange(next: VideoSource) {
    setSource(next)
    writeVideoSource(next)
    load(next)
  }

  const list = (videos || []).filter((v) => !q || `${v.title.name} ${v.name}`.toLowerCase().includes(q.toLowerCase()))

  return (
    <>
      <TopBar step="videos" nav="videos" />
      <main className="wrap">
        <div className="mk-head">
          <div>
            <div className="eyebrow">Creator marketplace</div>
            <h1 style={{ marginTop: 6 }}>Choose a video</h1>
          </div>
          <div className="mk-head-tools">
            <VideoSourceToggle value={source} onChange={onSourceChange} />
            <label className="search">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
              <input placeholder="Search videos" value={q} onChange={(e) => setQ(e.target.value)} />
            </label>
          </div>
        </div>

        {error ? <p className="error">Couldn’t load videos: {error}</p> : null}
        {videos === null && !error ? (
          <div className="grid">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="card"><div className="thumb" /><div className="sk" style={{ width: "70%" }} /></div>
            ))}
          </div>
        ) : null}
        {videos && !list.length ? (
          <div className="empty">
            <b>{q ? `No videos match “${q}”.` : "No videos yet."}</b>
            <span>{q ? "Try another title." : "Creators’ uploads show up here once they’re scanned for placement spots."}</span>
          </div>
        ) : null}

        <div className="grid">
          {list.map((v) => (
            <button key={v.id} className="card" onClick={() => setOpen(v)}>
              <VideoThumb v={v}>
                {v.duration_s ? <span className="dur">{fmt(v.duration_s, false)}</span> : null}
              </VideoThumb>
              <div className="card-meta">
                <span className="av">{initials(v.title.name)}</span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="card-t">{label(v)}</div>
                  <div className="card-s">{v.title.kind === "series" ? v.name : v.title.attribution || v.name}</div>
                  <div className="card-row">
                    {isScanned(v) ? (
                      <span className="tag"><span className="dot" />Scanned</span>
                    ) : (
                      <span className="tag tag-amber">Scanning…</span>
                    )}
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      </main>
      {open ? <Detail v={open} onClose={() => setOpen(null)} /> : null}
    </>
  )
}

function Detail({ v, onClose }: { v: Video; onClose: () => void }) {
  const [moments, setMoments] = useState<CatalogMoment[] | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    listMoments(v.id, session.get()?.brandId)
      .then(setMoments)
      .catch((e) => setError(String(e.message || e)))
  }, [v.id])

  const usable = (moments || []).filter((m) => !m.brand_fit?.vetoed)
  const vetoed = (moments || []).length - usable.length
  const dur = v.duration_s || Math.max(1, ...usable.map((m) => m.end_s))

  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer" role="dialog" aria-label={label(v)}>
        <div className="dr-top">
          <span className="eyebrow">{v.title.kind}</span>
          <button className="x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <VideoThumb v={v}>{v.duration_s ? <span className="dur">{fmt(v.duration_s, false)}</span> : null}</VideoThumb>
        <div>
          <h2 style={{ fontSize: 24 }}>{label(v)}</h2>
          {v.title.synopsis ? <p className="muted" style={{ marginTop: 6 }}>{v.title.synopsis}</p> : null}
          {v.title.attribution ? <p className="card-s" style={{ marginTop: 6 }}>{v.title.attribution}</p> : null}
        </div>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h3>Placement spots</h3>
            {vetoed ? <span className="muted" style={{ fontSize: 12 }}>{vetoed} hidden: off-brand for you</span> : null}
          </div>
          <div className="scrub">
            {usable.map((m) => <b key={m.id} style={{ left: `${(m.start_s / dur) * 100}%` }} title={`${m.whats_there} · ${fmt(m.start_s)}`} />)}
          </div>
          <div className="scrub-l mono"><span>00:00</span><span>{fmt(dur, false)}</span></div>
        </div>
        {error ? <p className="error">{error}</p> : null}
        {moments === null && !error ? <div className="sk" style={{ width: "60%" }} /> : null}
        {moments && !usable.length ? (
          <p className="muted">
            {isScanned(v) ? "No spots in this video fit your brand." : "Still scanning this video for spots. Check back in a minute."}
          </p>
        ) : null}
        <div className="spots">
          {usable.map((m) => {
            const r = regionPct(m)
            const frame = mediaUrl(m.frame_key)
            return (
              <div key={m.id} className="spot">
                <div className="thumb">
                  {frame ? <img src={frame} alt="" /> : <div className="thumb-ph" />}
                  {r ? <div className="det" style={r} /> : null}
                </div>
                <div style={{ minWidth: 0 }}>
                  <b style={{ fontWeight: 600, textTransform: "capitalize" }}>{m.whats_there || "Spot"}</b>
                  <div className="card-s mono">{fmt(m.start_s)} – {fmt(m.end_s)}</div>
                </div>
                <span className="conf">{(m.brand_fit?.total ?? m.score ?? 0).toFixed(2)}</span>
              </div>
            )
          })}
        </div>
        <div className="dr-foot">
          <span className="muted" style={{ fontSize: 12.5 }}>No faces are edited. You approve every placement.</span>
          {usable.length ? (
            <Link className="btn btn-primary btn-lg" href={`/review/${v.title_id}/${v.id}`}>
              Review placements →
            </Link>
          ) : (
            <button className="btn btn-primary btn-lg" disabled>Review placements →</button>
          )}
        </div>
      </div>
    </div>
  )
}
