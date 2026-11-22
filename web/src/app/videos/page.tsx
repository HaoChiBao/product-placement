"use client"

import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

import { AnimatedGroup } from "@/components/motion-primitives/animated-group"
import { TextEffect } from "@/components/motion-primitives/text-effect"
import {
  MorphingDialog,
  MorphingDialogClose,
  MorphingDialogContainer,
  MorphingDialogContent,
  MorphingDialogDescription,
  MorphingDialogTitle,
  MorphingDialogTrigger,
} from "@/components/motion-primitives/morphing-dialog"
import { TopBar } from "@/components/top-bar"
import { Badge, StatusDot } from "@/components/ui/badge"
import { Button, ButtonLink } from "@/components/ui/button"
import { EmptyState, Skeleton } from "@/components/ui/empty-state"
import { PageHeader, PageShell } from "@/components/ui/page-shell"
import { VideoThumb, videoLabel as label } from "@/components/video-thumb"
import {
  fmt,
  isScanned,
  listMoments,
  listVideos,
  mediaUrl,
  regionPct,
  session,
  type CatalogMoment,
  type Video,
} from "@/lib/catalog-client"

function initials(s: string) {
  return s
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("")
}

export default function VideosPage() {
  const router = useRouter()
  const [videos, setVideos] = useState<Video[] | null>(null)
  const [error, setError] = useState("")
  const [q, setQ] = useState("")

  useEffect(() => {
    if (!session.get()) {
      router.replace("/")
      return
    }
    listVideos().then(setVideos).catch((e) => setError(String(e.message || e)))
  }, [router])

  const list = (videos || []).filter(
    (v) => !q || `${v.title.name} ${v.name}`.toLowerCase().includes(q.toLowerCase())
  )

  return (
    <>
      <TopBar step="videos" nav="videos" />
      <PageShell>
        <PageHeader
          eyebrow="Creator marketplace"
          title={
            <TextEffect
              as="h1"
              preset="fade-in-blur"
              per="word"
              className="font-display text-[clamp(26px,3.2vw,36px)] font-bold leading-[1.1] tracking-tight"
            >
              Choose a video
            </TextEffect>
          }
          action={
            <label className="flex min-w-[min(340px,100%)] items-center gap-2 rounded-md border border-border bg-card px-3">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
              <input
                className="min-w-0 flex-1 border-0 bg-transparent py-2.5 outline-none"
                placeholder="Search videos"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </label>
          }
        />

        {error ? <p className="mb-4 text-[13px] text-destructive">Couldn’t load videos: {error}</p> : null}
        {videos === null && !error ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-x-5 gap-y-7">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="flex flex-col gap-2.5">
                <div className="thumb" />
                <Skeleton className="w-[70%]" />
              </div>
            ))}
          </div>
        ) : null}
        {videos && !list.length ? (
          <EmptyState
            title={q ? `No videos match “${q}”.` : "No videos yet."}
            description={
              q ? "Try another title." : "Creators’ uploads show up here once they’re scanned for placement spots."
            }
          />
        ) : null}

        <AnimatedGroup
          preset="blur-slide"
          className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-x-5 gap-y-7"
          variants={{
            container: {
              hidden: { opacity: 0 },
              visible: {
                opacity: 1,
                transition: { staggerChildren: 0.05 },
              },
            },
            item: {
              hidden: { opacity: 0, y: 12, filter: "blur(4px)" },
              visible: {
                opacity: 1,
                y: 0,
                filter: "blur(0px)",
                transition: { duration: 0.28, ease: [0.22, 1, 0.36, 1] },
              },
            },
          }}
        >
          {list.map((v) => (
            <VideoCard key={v.id} v={v} />
          ))}
        </AnimatedGroup>
      </PageShell>
    </>
  )
}

function VideoCard({ v }: { v: Video }) {
  return (
    <MorphingDialog
      transition={{ type: "spring", bounce: 0.05, duration: 0.4 }}
    >
      <MorphingDialogTrigger className="flex w-full min-w-0 flex-col gap-2.5 border-0 bg-transparent p-0 text-left">
        <VideoThumb v={v} preview>
          {v.duration_s ? (
            <span className="absolute bottom-2 right-2 rounded-[5px] bg-black/72 px-1.5 py-1 font-mono text-[11px] font-medium text-white">
              {fmt(v.duration_s, false)}
            </span>
          ) : null}
        </VideoThumb>
        <div className="flex min-w-0 gap-2.5">
          <span className="grid size-[30px] shrink-0 place-items-center rounded-full bg-[#3b6e8f] text-[11px] font-semibold text-white">
            {initials(v.title.name)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="line-clamp-2 font-semibold leading-snug">{label(v)}</div>
            <div className="mt-0.5 text-[12.5px] text-muted-foreground">
              {v.title.kind === "series" ? v.name : v.title.attribution || v.name}
            </div>
            <div className="mt-1.5">
              {isScanned(v) ? (
                <Badge>
                  <StatusDot />
                  Scanned
                </Badge>
              ) : (
                <Badge variant="warning">Scanning…</Badge>
              )}
            </div>
          </div>
        </div>
      </MorphingDialogTrigger>
      <MorphingDialogContainer>
        <MorphingDialogContent className="relative max-h-[90vh] w-[min(620px,100%)] overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-soft">
          <MorphingDialogClose className="absolute right-4 top-4 grid size-8 place-items-center rounded-sm border border-border bg-card" />
          <VideoDetail v={v} />
        </MorphingDialogContent>
      </MorphingDialogContainer>
    </MorphingDialog>
  )
}

function VideoDetail({ v }: { v: Video }) {
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
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between pr-10">
        <span className="eyebrow">{v.title.kind}</span>
      </div>
      <VideoThumb v={v}>
        {v.duration_s ? (
          <span className="absolute bottom-2 right-2 rounded-[5px] bg-black/72 px-1.5 py-1 font-mono text-[11px] font-medium text-white">
            {fmt(v.duration_s, false)}
          </span>
        ) : null}
      </VideoThumb>
      <div>
        <MorphingDialogTitle className="font-display text-2xl font-bold tracking-tight">
          {label(v)}
        </MorphingDialogTitle>
        <MorphingDialogDescription className="mt-0" disableLayoutAnimation>
          {v.title.synopsis ? <p className="mt-1.5 text-muted-foreground">{v.title.synopsis}</p> : null}
          {v.title.attribution ? <p className="mt-1.5 text-[12.5px] text-muted-foreground">{v.title.attribution}</p> : null}
        </MorphingDialogDescription>
      </div>
      <div>
        <div className="flex items-baseline justify-between">
          <h3>Placement spots</h3>
          {vetoed ? (
            <span className="text-xs text-muted-foreground">{vetoed} hidden: off-brand for you</span>
          ) : null}
        </div>
        <div className="relative mt-2.5 h-[30px] rounded-xs bg-muted">
          {usable.map((m) => (
            <b
              key={m.id}
              className="absolute bottom-1 top-1 w-1.5 -ml-0.5 rounded-sm bg-accent font-normal"
              style={{ left: `${(m.start_s / dur) * 100}%` }}
              title={`${m.whats_there} · ${fmt(m.start_s)}`}
            />
          ))}
        </div>
        <div className="mt-1 flex justify-between font-mono text-[11px] text-faint">
          <span>00:00</span>
          <span>{fmt(dur, false)}</span>
        </div>
      </div>
      {error ? <p className="text-[13px] text-destructive">{error}</p> : null}
      {moments === null && !error ? <Skeleton className="w-[60%]" /> : null}
      {moments && !usable.length ? (
        <p className="text-muted-foreground">
          {isScanned(v)
            ? "No spots in this video fit your brand."
            : "Still scanning this video for spots. Check back in a minute."}
        </p>
      ) : null}
      <div className="flex flex-col gap-2">
        {usable.map((m) => {
          const r = regionPct(m)
          const frame = mediaUrl(m.frame_key)
          return (
            <div
              key={m.id}
              className="grid grid-cols-[120px_1fr_auto] items-center gap-3 rounded-md border border-border p-2 max-sm:grid-cols-[96px_1fr]"
            >
              <div className="thumb rounded-xs">
                {frame ? <img src={frame} alt="" /> : <div className="thumb-ph" />}
                {r ? <div className="det" style={r} /> : null}
              </div>
              <div className="min-w-0">
                <b className="font-semibold capitalize">{m.whats_there || "Spot"}</b>
                <div className="font-mono text-[12.5px] text-muted-foreground">
                  {fmt(m.start_s)} – {fmt(m.end_s)}
                </div>
              </div>
              <span className="font-mono text-xs text-muted-foreground max-sm:hidden">
                {(m.brand_fit?.total ?? m.score ?? 0).toFixed(2)}
              </span>
            </div>
          )
        })}
      </div>
      <div className="sticky bottom-0 -mb-6 flex flex-wrap items-center justify-between gap-3 border-t border-border bg-card py-3.5 pb-6">
        <span className="text-[12.5px] text-muted-foreground">No faces are edited. You approve every placement.</span>
        {usable.length ? (
          <ButtonLink variant="primary" size="lg" href={`/review/${v.title_id}/${v.id}`}>
            Review placements →
          </ButtonLink>
        ) : (
          <Button variant="primary" size="lg" disabled>
            Review placements →
          </Button>
        )}
      </div>
    </div>
  )
}
