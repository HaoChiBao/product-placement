"use client"

import { useParams, useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { PlacementIsland } from "@/components/placement-island"
import { TopBar, useToast } from "@/components/top-bar"
import { Badge } from "@/components/ui/badge"
import { Button, ButtonLink } from "@/components/ui/button"
import { Panel } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/empty-state"
import { videoLabel } from "@/components/video-thumb"
import { playbackEnd, playbackOrigin } from "@/lib/clip-window"
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
import { cn } from "@/lib/utils"

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

function statusBadgeVariant(st: Status): "muted" | "accent" | "warning" | "danger" {
  if (st === "ready") return "accent"
  if (st === "queued" || st === "rendering" || st === "approved") return "warning"
  if (st === "rejected" || st === "failed") return "danger"
  return "muted"
}

const PLAY = (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
    <path d="M2 1l9 5-9 5z" />
  </svg>
)
const PAUSE = (
  <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
    <rect x="2" y="1" width="3" height="10" />
    <rect x="7" y="1" width="3" height="10" />
  </svg>
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
  const placement = m ? placements[m.id] : undefined
  const clip = m && st === "ready" ? mediaUrl(placement?.clip_key) : null
  const showClip = Boolean(clip) && !holding
  const origin = m ? playbackOrigin(m.start_s, placement?.clip_start_s) : 0
  const episodeSrc = mediaUrl(video?.video_key)
  const dur = video?.duration_s || Math.max(1, ...list.map((x) => x.end_s))

  useEffect(() => {
    setPlaying(false)
    if (m) setT(showClip ? origin : m.start_s)
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
    const now = showClip ? origin + el.currentTime : el.currentTime
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
    const nearest = list.reduce(
      (best, x, i) => (Math.abs(x.start_s - at) < Math.abs(list[best].start_s - at) ? i : best),
      0
    )
    setCur(nearest)
  }

  async function decide(mm: CatalogMoment, status: "approved" | "rejected") {
    setBusy(mm.id)
    try {
      const p = await decidePlacement(brandId, mm.id, status)
      setPlacements((ps) => ({ ...ps, [mm.id]: p }))
      if (status === "approved") {
        const err = p.error?.toLowerCase() || ""
        const held = err.includes("key missing")
        showToast(
          held
            ? err.includes("higgsfield")
              ? "Queued — generation waiting on Higgsfield API key"
              : err.includes("s3")
                ? "Queued — generation waiting on S3"
                : "Queued — generation waiting on OpenRouter API key"
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
          <div className="flex flex-wrap items-center gap-3.5 border-b border-stage-line px-4 py-2.5">
            <ButtonLink href="/videos" variant="stage" size="sm">
              ← Videos
            </ButtonLink>
            <div className="min-w-0">
              <b className="font-semibold">{video ? videoLabel(video) : "Loading…"}</b>
              <div className="font-mono text-[11.5px] tabular-nums text-stage-muted">
                {list.length} spot{list.length === 1 ? "" : "s"} · {fmt(dur, false)}
              </div>
            </div>
            <span className="flex-1" />
            <Button variant="stage" size="sm" onClick={saveDraft}>
              Save to drafts
            </Button>
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
                <video
                  key={episodeSrc}
                  ref={vidRef}
                  src={episodeSrc}
                  playsInline
                  preload="metadata"
                  onTimeUpdate={onTime}
                />
              ) : frame ? (
                <img src={frame} alt="" />
              ) : null}

              {!showClip && box && !playing ? (
                <div className="det" style={box}>
                  <span>{m?.whats_there || "spot"}</span>
                </div>
              ) : null}

              {m && !showClip && !holding && IN_FLIGHT.includes(st) ? (
                <div className="absolute inset-0 bg-black/35">
                  <div className="scan" />
                </div>
              ) : null}

              {m ? (
                <PlacementIsland
                  status={
                    IN_FLIGHT.includes(st)
                      ? "generating"
                      : st === "ready" || st === "rejected" || st === "failed"
                        ? st
                        : "pending"
                  }
                  index={cur + 1}
                  total={list.length}
                  what={m.whats_there || "spot"}
                  message={
                    /no product asset/i.test(placements[m.id]?.error || "")
                      ? "No product image on file. Upload one to generate this placement."
                      : placements[m.id]?.error || "Try again from the list."
                  }
                />
              ) : null}

              {m ? (
                <span className="absolute bottom-2.5 left-2.5 rounded px-[7px] py-[5px] font-mono text-[11px] font-medium text-white bg-black/65">
                  {showClip
                    ? `Post-processed · ${m.whats_there}`
                    : `${m.whats_there || "spot"} · ${fmt(m.start_s)}`}
                </span>
              ) : null}
              {clip ? (
                <button
                  type="button"
                  className="absolute bottom-2.5 right-2.5 select-none rounded-[5px] border-0 bg-black/65 px-[9px] py-1.5 text-[11px] font-medium text-white"
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

          <div className="flex flex-col gap-3 border-t border-stage-line px-4 pb-[18px] pt-2.5">
            <div className="flex flex-wrap items-center gap-2.5 text-[12.5px]">
              <button
                type="button"
                className="grid size-[30px] place-items-center rounded-full border-0 bg-stage-ink text-stage disabled:opacity-40"
                onClick={togglePlay}
                aria-label={playing ? "Pause" : "Play"}
                disabled={!m}
              >
                {playing ? PAUSE : PLAY}
              </button>
              <Button
                variant="stage"
                size="sm"
                onClick={() => setCur((c) => Math.max(0, c - 1))}
                disabled={cur === 0}
              >
                ‹ Prev
              </Button>
              <Button
                variant="stage"
                size="sm"
                onClick={() => setCur((c) => Math.min(list.length - 1, c + 1))}
                disabled={cur >= list.length - 1}
              >
                Next ›
              </Button>
              <span className="font-mono tabular-nums text-stage-ink">{fmt(t)}</span>
              <span className="font-mono tabular-nums text-stage-muted">/ {fmt(dur)}</span>
              <span className="flex-1" />
              <span className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-stage-muted">
                <i className="ml-2 size-2 rounded-sm bg-stage-muted not-italic" />
                Pending
                <i className="ml-2 size-2 rounded-sm bg-[#e6a24a] not-italic" />
                Generating
                <i className="ml-2 size-2 rounded-sm bg-success not-italic" />
                Ready
                <i className="ml-2 size-2 rounded-sm bg-[#e5484d] not-italic" />
                Rejected
              </span>
            </div>
            <div className="ap-time" onClick={seekBar}>
              <div className="ap-rail">
                <div className="ap-done" style={{ width: `${(t / dur) * 100}%` }} />
              </div>
              {list.map((x, i) => (
                <button
                  key={x.id}
                  type="button"
                  className={cn("ap-mk", i === cur && "cur")}
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
                  <span
                    key={i}
                    style={{ left: `${Math.min(97, Math.max(3, (i / 6) * 100))}%` }}
                  >
                    {fmt((dur * i) / 6, false)}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col border-l border-border bg-background max-[1080px]:min-h-[520px] max-[1080px]:border-l-0 max-[1080px]:border-t">
          <div className="flex items-end justify-between gap-2.5 border-b border-border p-4">
            <div>
              <div className="eyebrow">Review</div>
              <h2 className="mt-0.5">Approve each placement</h2>
            </div>
            <Button
              size="sm"
              onClick={approveAll}
              disabled={!counts.pending || Boolean(busy)}
            >
              Approve all
            </Button>
          </div>

          <div className="flex flex-1 flex-col gap-2.5 overflow-y-auto p-3">
            {!hasAsset ? (
              <Panel className="flex flex-col gap-2 p-3 text-[13px]">
                <b className="font-semibold">Add a product image to generate placements</b>
                <span className="text-muted-foreground">
                  We need a picture of your product to place it in the video.
                </span>
                <ButtonLink href="/" size="sm" className="self-start">
                  Upload product image
                </ButtonLink>
              </Panel>
            ) : null}
            {error ? <p className="text-[13px] text-destructive">{error}</p> : null}
            {moments === null && !error
              ? Array.from({ length: 3 }, (_, i) => (
                  <div
                    key={i}
                    className="flex flex-col gap-2.5 rounded-md border-[1.5px] border-border bg-card p-2.5"
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="thumb w-24 shrink-0" />
                      <div className="flex flex-1 flex-col gap-1.5">
                        <Skeleton className="w-[80%]" />
                        <Skeleton className="w-1/2" />
                      </div>
                    </div>
                  </div>
                ))
              : null}
            {moments && !list.length ? (
              <p className="text-muted-foreground">
                {video && isScanned(video)
                  ? "No spots in this video fit your brand."
                  : "Still scanning this video for placement spots. This page updates on its own."}
              </p>
            ) : null}
            {list.map((x, i) => {
              const s = statusOf(x)
              const f = mediaUrl(x.frame_key)
              const r = regionPct(x)
              return (
                <div
                  key={x.id}
                  data-st={s}
                  className={cn(
                    "flex flex-col gap-2.5 rounded-md border-[1.5px] border-border bg-card p-2.5 text-left",
                    i === cur && "border-foreground",
                    s === "ready" && "border-success",
                    (s === "rejected" || s === "failed") && "border-destructive"
                  )}
                >
                  <button
                    type="button"
                    className="flex w-full items-center gap-2.5 border-0 bg-transparent p-0 text-left"
                    onClick={() => setCur(i)}
                  >
                    <div className="thumb w-24 shrink-0 !rounded-sm">
                      {f ? <img src={f} alt="" /> : <div className="thumb-ph" />}
                      {r ? <div className="det" style={r} /> : null}
                    </div>
                    <div className="min-w-0 flex-1">
                      <b className="block text-[13px] font-semibold capitalize">
                        {i + 1}. {x.whats_there || "Spot"}
                      </b>
                      <small className="font-mono text-[11.5px] leading-[1.4] text-muted-foreground">
                        {fmt(playbackOrigin(x.start_s, placements[x.id]?.clip_start_s))} –{" "}
                        {fmt(playbackEnd(x.end_s, placements[x.id]?.clip_end_s))}
                      </small>
                    </div>
                    <Badge variant={statusBadgeVariant(s)} className="ml-auto shrink-0">
                      {statusLabel(s, placements[x.id])}
                    </Badge>
                  </button>
                  {placements[x.id]?.error ? (
                    <p className="m-0 text-xs leading-[1.45] text-muted-foreground">
                      {placements[x.id].error}
                    </p>
                  ) : null}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      className={cn(
                        "flex-1 rounded-sm border border-border bg-card px-2 py-[7px] text-[12.5px] font-medium disabled:cursor-not-allowed disabled:opacity-50",
                        (s === "ready" ||
                          (IN_FLIGHT.includes(s) && !placements[x.id]?.error)) &&
                          "border-accent bg-accent text-white"
                      )}
                      aria-pressed={
                        s === "ready" || (IN_FLIGHT.includes(s) && !placements[x.id]?.error)
                      }
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
                      type="button"
                      className={cn(
                        "flex-1 rounded-sm border border-border bg-card px-2 py-[7px] text-[12.5px] font-medium disabled:cursor-not-allowed disabled:opacity-50",
                        s === "rejected" && "border-destructive bg-destructive text-white"
                      )}
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

          <div className="flex flex-col gap-2.5 border-t border-border px-4 py-3.5">
            <div className="flex justify-between gap-3 text-[13px]">
              <span>
                {counts.ready} ready · {counts.flight} generating · {counts.rejected} rejected ·{" "}
                {counts.pending} pending
              </span>
            </div>
            <ButtonLink
              className="w-full"
              variant="primary"
              size="lg"
              href="/campaigns"
              onClick={() => session.removeDraft(episodeId)}
              aria-disabled={counts.pending > 0}
              style={counts.pending ? { opacity: 0.45, pointerEvents: "none" } : undefined}
            >
              {counts.pending
                ? `Review ${counts.pending} more to finish`
                : "Done, view campaigns"}
            </ButtonLink>
            <p className="text-xs leading-[1.45] text-muted-foreground">
              {!gen
                ? "Approving queues generation."
                : gen.ready
                  ? gen.method === "higgsfield"
                    ? "Approving runs the real placement pipeline (Qwen edit + Seedance)."
                    : "Approving runs a full-clip Wan 3.0 swap."
                  : gen.mock
                    ? "Approving runs mock generation (MOCK_HIGGSFIELD is on)."
                    : gen.method === "higgsfield"
                      ? "Approving queues generation. Without a Higgsfield API key the placement stays queued — no fake clip is created."
                      : (gen.reason || "").toLowerCase().includes("s3")
                        ? "Approving queues generation. Without S3 the placement stays queued — no fake clip is created."
                        : "Approving queues generation. Without an OpenRouter API key the placement stays queued — no fake clip is created."}
            </p>
          </div>
        </aside>
      </main>
      {toast}
    </>
  )
}
