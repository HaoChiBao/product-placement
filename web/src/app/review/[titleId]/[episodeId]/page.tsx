"use client"

import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { TopBar, useToast } from "@/components/top-bar"
import { videoLabel } from "@/components/video-thumb"
import {
  decidePlacement,
  fmt,
  getGenerationInfo,
  isScanned,
  getVideo,
  listMoments,
  listPlacements,
  mediaUrl,
  regionPct,
  saveCampaign,
  session,
  type CatalogMoment,
  type GenerationInfo,
  type Placement,
  type Video,
} from "@/lib/catalog-client"

type Status = Placement["status"] | "pending"

const STATUS_LABEL: Record<Status, string> = {
  pending: "Pending",
  approved: "Queued",
  queued: "Queued",
  rendering: "Generating",
  ready: "Ready",
  rejected: "Rejected",
  failed: "Failed",
}
const IN_FLIGHT: Status[] = ["approved", "queued", "rendering"]

function statusLabel(st: Status, p?: Placement): string {
  if (st === "queued" && p?.error?.toLowerCase().includes("key missing")) {
    return "Key missing"
  }
  return STATUS_LABEL[st]
}

const PLAY = <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor"><path d="M2 1l9 5-9 5z" /></svg>
const PAUSE = (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor"><rect x="2" y="1" width="3" height="10" /><rect x="7" y="1" width="3" height="10" /></svg>
)

export default function ReviewPage() {
  const { titleId, episodeId } = useParams<{ titleId: string; episodeId: string }>()
  const router = useRouter()
  const [brandId, setBrandId] = useState("")
  const [hasAsset, setHasAsset] = useState(true)
  const [video, setVideo] = useState<Video | null>(null)
  const [moments, setMoments] = useState<CatalogMoment[] | null>(null)
  const [placements, setPlacements] = useState<Record<string, Placement>>({})
  const [error, setError] = useState("")
  const [cur, setCur] = useState(0)
  const [t, setT] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [holding, setHolding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [gen, setGen] = useState<GenerationInfo | null>(null)
  const [toast, showToast] = useToast()
  const vidRef = useRef<HTMLVideoElement>(null)

  const refresh = useCallback(
    async (bid: string) => {
      const [ms, ps] = await Promise.all([listMoments(episodeId, bid), listPlacements(bid, episodeId)])
      setMoments(ms.filter((m) => !m.brand_fit?.vetoed).sort((a, b) => a.start_s - b.start_s))
      setPlacements(Object.fromEntries(ps.map((p) => [p.moment_id, p])))
    },
    [episodeId]
  )

  useEffect(() => {
    const s = session.get()
    if (!s) {
      router.replace("/")
      return
    }
    setBrandId(s.brandId)
    setHasAsset(s.hasAsset !== false)
    getVideo(titleId, episodeId)
      .then((v) => (v ? setVideo(v) : setError("This video no longer exists.")))
      .catch((e) => setError(String(e.message || e)))
    refresh(s.brandId).catch((e) => setError(String(e.message || e)))
    getGenerationInfo().then(setGen).catch(() => setGen(null))
  }, [titleId, episodeId, router, refresh])

  const statusOf = useCallback((m: CatalogMoment): Status => placements[m.id]?.status || "pending", [placements])
  const needsPoll =
    (moments || []).some((m) => IN_FLIGHT.includes(statusOf(m))) || (video && !isScanned(video))

  useEffect(() => {
    if (!brandId || !needsPoll) return
    const id = window.setInterval(() => refresh(brandId).catch(() => {}), 4000)
    return () => window.clearInterval(id)
  }, [brandId, needsPoll, refresh])

  const list = useMemo(() => moments || [], [moments])
  const m = list[cur]
  const st = m ? statusOf(m) : "pending"
  const clip = m && st === "ready" ? mediaUrl(placements[m.id]?.clip_key) : null
  const showClip = Boolean(clip) && !holding
  const episodeSrc = mediaUrl(video?.video_key)
  const dur = video?.duration_s || Math.max(1, ...list.map((x) => x.end_s))

  // Point the stage at the selected moment.
  useEffect(() => {
    setPlaying(false)
    if (m) setT(m.start_s)
  }, [m?.id, showClip]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = vidRef.current
    if (!el || !m) return
    if (!showClip) el.currentTime = m.start_s
    else el.currentTime = 0
  }, [m?.id, showClip, episodeSrc]) // eslint-disable-line react-hooks/exhaustive-deps

  function onTime() {
    const el = vidRef.current
    if (!el || !m) return
    const now = showClip ? m.start_s + el.currentTime : el.currentTime
    setT(now)
    if (!showClip && now >= m.end_s) {
      el.pause()
      el.currentTime = m.start_s
      setPlaying(false)
    }
  }

  function togglePlay() {
    const el = vidRef.current
    if (!el) return
    if (el.paused) el.play().then(() => setPlaying(true)).catch(() => setPlaying(false))
    else {
      el.pause()
      setPlaying(false)
    }
  }

  function seekBar(e: React.MouseEvent<HTMLDivElement>) {
    if ((e.target as HTMLElement).closest(".ap-mk")) return
    const r = e.currentTarget.getBoundingClientRect()
    const at = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur
    const nearest = list.reduce((best, x, i) => (Math.abs(x.start_s - at) < Math.abs(list[best].start_s - at) ? i : best), 0)
    setCur(nearest)
  }

  async function decide(mm: CatalogMoment, status: "approved" | "rejected") {
    setBusy(mm.id)
    try {
      const p = await decidePlacement(brandId, mm.id, status)
      setPlacements((ps) => ({ ...ps, [mm.id]: p }))
      if (status === "approved") {
        const held = Boolean(p.error?.toLowerCase().includes("key missing"))
        showToast(
          held
            ? "Queued — generation waiting on Higgsfield API key"
            : "Placing your product in this moment…"
        )
        await saveCampaign({ brandId, episodeId, titleId, status: "in_progress" }).catch(() => {})
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function approveAll() {
    for (const mm of list.filter((x) => statusOf(x) === "pending")) await decide(mm, "approved")
  }

  async function saveDraft() {
    try {
      await saveCampaign({ brandId, episodeId, titleId, status: "draft" })
      showToast("Saved to drafts")
      router.push("/campaigns")
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e))
    }
  }

  const counts = list.reduce(
    (c, x) => {
      const s = statusOf(x)
      if (s === "ready") c.ready++
      else if (s === "rejected") c.rejected++
      else if (IN_FLIGHT.includes(s)) c.flight++
      else if (s === "failed") c.failed++
      else c.pending++
      return c
    },
    { ready: 0, rejected: 0, flight: 0, failed: 0, pending: 0 }
  )

  const frame = m ? mediaUrl(m.frame_key) : null
  const box = m ? regionPct(m) : null

  return (
    <>
      <TopBar step="review" />
      <main className="ap">
        <section className="ap-main">
          <div className="ap-bar">
            <Link className="sbtn" href="/videos">← Videos</Link>
            <div style={{ minWidth: 0 }}>
              <b style={{ fontWeight: 600 }}>{video ? videoLabel(video) : "Loading…"}</b>
              <div className="ap-sub mono">
                {list.length} spot{list.length === 1 ? "" : "s"} · {fmt(dur, false)}
              </div>
            </div>
            <span className="sp" />
            <button className="sbtn" onClick={saveDraft}>Save to drafts</button>
          </div>

          <div className="stage-wrap">
            <div className="stage">
              {showClip ? (
                <video
                  key={clip!}
                  ref={vidRef}
                  src={clip!}
                  playsInline
                  loop
                  onTimeUpdate={onTime}
                  onPause={() => setPlaying(false)}
                />
              ) : episodeSrc && !holding ? (
                <video key={episodeSrc} ref={vidRef} src={episodeSrc} playsInline preload="metadata" onTimeUpdate={onTime} />
              ) : frame ? (
                <img src={frame} alt="" />
              ) : null}

              {!showClip && box && !playing ? (
                <div className="det" style={box}>
                  <span>{m?.whats_there || "spot"}</span>
                </div>
              ) : null}

              {m && !showClip && !holding && st !== "pending" ? (
                <div className="stage-msg">
                  {IN_FLIGHT.includes(st) ? (
                    <>
                      <div className="scan" />
                      <div><span className="spinner" />Placing your product in this moment…</div>
                    </>
                  ) : st === "rejected" ? (
                    <div>Rejected · original kept</div>
                  ) : st === "failed" ? (
                    <div>
                      {/no product asset/i.test(placements[m.id]?.error || "")
                        ? "No product image on file. Upload one to generate this placement."
                        : `Generation failed${placements[m.id]?.error ? `: ${placements[m.id]?.error}` : ""}`}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {m ? (
                <span className="stage-tag">
                  {showClip ? `Post-processed · ${m.whats_there}` : `${m.whats_there || "spot"} · ${fmt(m.start_s)}`}
                </span>
              ) : null}
              {clip ? (
                <button
                  className="ap-hold"
                  onMouseDown={() => setHolding(true)}
                  onMouseUp={() => setHolding(false)}
                  onMouseLeave={() => setHolding(false)}
                  onTouchStart={() => setHolding(true)}
                  onTouchEnd={() => setHolding(false)}
                >
                  Hold to see original
                </button>
              ) : null}
            </div>
          </div>

          <div className="ap-tbar">
            <div className="ap-ctrl">
              <button className="play" onClick={togglePlay} aria-label={playing ? "Pause" : "Play"} disabled={!m}>
                {playing ? PAUSE : PLAY}
              </button>
              <button className="sbtn" onClick={() => setCur((c) => Math.max(0, c - 1))} disabled={cur === 0}>‹ Prev</button>
              <button className="sbtn" onClick={() => setCur((c) => Math.min(list.length - 1, c + 1))} disabled={cur >= list.length - 1}>
                Next ›
              </button>
              <span className="mono tc">{fmt(t)}</span>
              <span className="mono" style={{ color: "var(--stage-muted)" }}>/ {fmt(dur)}</span>
              <span style={{ flex: 1 }} />
              <span className="ap-legend">
                <i style={{ background: "var(--stage-muted)" }} />Pending
                <i style={{ background: "#e6a24a" }} />Generating
                <i style={{ background: "var(--key)" }} />Ready
                <i style={{ background: "#e5484d" }} />Rejected
              </span>
            </div>
            <div className="ap-time" onClick={seekBar}>
              <div className="ap-rail"><div className="ap-done" style={{ width: `${(t / dur) * 100}%` }} /></div>
              {list.map((x, i) => (
                <button
                  key={x.id}
                  className={`ap-mk ${i === cur ? "cur" : ""}`}
                  data-st={statusOf(x)}
                  style={{ left: `${(x.start_s / dur) * 100}%`, width: `${Math.max(14, 0)}px` }}
                  onClick={() => setCur(i)}
                  aria-label={`Spot ${i + 1} at ${fmt(x.start_s)}`}
                >
                  <span>{i + 1}</span>
                </button>
              ))}
              <div className="ap-head" style={{ left: `${(t / dur) * 100}%` }} />
              <div className="ap-ticks">
                {Array.from({ length: 7 }, (_, i) => (
                  <span key={i} style={{ left: `${Math.min(97, Math.max(3, (i / 6) * 100))}%` }}>{fmt((dur * i) / 6, false)}</span>
                ))}
              </div>
            </div>
          </div>
        </section>

        <aside className="ap-side">
          <div className="ap-side-h">
            <div>
              <div className="eyebrow">Review</div>
              <h2 style={{ marginTop: 2 }}>Approve each placement</h2>
            </div>
            <button className="btn btn-sm" onClick={approveAll} disabled={!counts.pending || Boolean(busy)}>Approve all</button>
          </div>
          <div className="ap-list">
            {!hasAsset ? (
              <div className="panel" style={{ padding: 12, fontSize: 13, display: "flex", flexDirection: "column", gap: 8 }}>
                <b style={{ fontWeight: 600 }}>Add a product image to generate placements</b>
                <span className="muted">We need a picture of your product to place it in the video.</span>
                <Link className="btn btn-sm" href="/" style={{ alignSelf: "flex-start" }}>Upload product image</Link>
              </div>
            ) : null}
            {error ? <p className="error">{error}</p> : null}
            {moments === null && !error
              ? Array.from({ length: 3 }, (_, i) => (
                  <div key={i} className="ed-it">
                    <div className="ed-top">
                      <div className="thumb" />
                      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
                        <div className="sk" style={{ width: "80%" }} />
                        <div className="sk" style={{ width: "50%" }} />
                      </div>
                    </div>
                  </div>
                ))
              : null}
            {moments && !list.length ? (
              <p className="muted">
                {(video && isScanned(video))
                  ? "No spots in this video fit your brand."
                  : "Still scanning this video for placement spots. This page updates on its own."}
              </p>
            ) : null}
            {list.map((x, i) => {
              const s = statusOf(x)
              const f = mediaUrl(x.frame_key)
              const r = regionPct(x)
              return (
                <div key={x.id} className={`ed-it ${i === cur ? "cur" : ""}`} data-st={s}>
                  <button className="ed-top" onClick={() => setCur(i)}>
                    <div className="thumb">
                      {f ? <img src={f} alt="" /> : <div className="thumb-ph" />}
                      {r ? <div className="det" style={r} /> : null}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <b style={{ textTransform: "capitalize" }}>{i + 1}. {x.whats_there || "Spot"}</b>
                      <small>{fmt(x.start_s)} – {fmt(x.end_s)}</small>
                    </div>
                    <span className="st-pill" data-st={s}>{statusLabel(s, placements[x.id])}</span>
                  </button>
                  {placements[x.id]?.error ? (
                    <p className="note" style={{ margin: "0 0 6px", gridColumn: "1 / -1" }}>
                      {placements[x.id].error}
                    </p>
                  ) : null}
                  <div className="ed-act">
                    <button
                      className="ok"
                      aria-pressed={s === "ready" || (IN_FLIGHT.includes(s) && !placements[x.id]?.error)}
                      disabled={
                        busy === x.id ||
                        s === "ready" ||
                        (IN_FLIGHT.includes(s) && !placements[x.id]?.error)
                      }
                      onClick={() => decide(x, "approved")}
                    >
                      ✓ {s === "failed" || placements[x.id]?.error ? "Retry" : "Approve"}
                    </button>
                    <button
                      className="no"
                      aria-pressed={s === "rejected"}
                      disabled={busy === x.id || s === "rejected"}
                      onClick={() => decide(x, "rejected")}
                    >
                      ✕ Reject
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="ap-foot">
            <div className="sum-row">
              <span>
                {counts.ready} ready · {counts.flight} generating · {counts.rejected} rejected · {counts.pending} pending
              </span>
            </div>
            <Link
              className="btn btn-primary btn-lg"
              href="/campaigns"
              onClick={() => session.removeDraft(episodeId)}
              aria-disabled={counts.pending > 0}
              style={counts.pending ? { opacity: 0.45, pointerEvents: "none" } : undefined}
            >
              {counts.pending ? `Review ${counts.pending} more to finish` : "Done, view campaigns"}
            </Link>
            <p className="note">
              {gen?.ready
                ? "Approving runs the real placement pipeline (Qwen edit + Seedance)."
                : gen?.mock
                  ? "Approving runs mock generation (MOCK_HIGGSFIELD is on)."
                  : "Approving queues generation. Without a Higgsfield API key the placement stays queued — no fake clip is created."}
            </p>
          </div>
        </aside>
      </main>
      {toast}
    </>
  )
}
