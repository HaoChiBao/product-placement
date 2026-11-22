"use client"

import { useEffect, useRef, useState } from "react"

import { placementTickBoxes } from "@/lib/placement-ticks"
import { cn } from "@/lib/utils"

export type PlacementWindow = {
  id: string
  start_s: number
  end_s: number
  clip_url: string | null
  brand_name?: string | null
}

type Props = {
  videoUrl: string
  posterUrl?: string | null
  placements: PlacementWindow[]
  durationS?: number | null
}

function formatClock(seconds: number) {
  const total = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, "0")}`
}

/**
 * Overlay approach: play the source episode, and during each ready placement
 * window swap in a synced overlay <video>. Chosen over full-episode pre-stitch
 * because placements are per-brand; stitching would need one rendition per brand.
 */
export function PlacementPlayer({ videoUrl, posterUrl, placements, durationS }: Props) {
  const shellRef = useRef<HTMLDivElement>(null)
  const mainRef = useRef<HTMLVideoElement>(null)
  const overlayRef = useRef<HTMLVideoElement>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const [showPlaced, setShowPlaced] = useState(true)
  const [paused, setPaused] = useState(true)
  const [muted, setMuted] = useState(false)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(durationS && durationS > 0 ? durationS : 0)
  const activeClip = useRef<string | null>(null)
  const readyCount = placements.filter((p) => p.clip_url).length
  const knownDuration = duration > 0 ? duration : durationS && durationS > 0 ? durationS : 0
  const ticks = placementTickBoxes(
    placements.map((placement) => ({
      id: placement.id,
      brand: placement.brand_name,
      start_s: placement.start_s,
      end_s: placement.end_s,
    })),
    knownDuration
  )
  const playedPct = knownDuration > 0 ? Math.min(100, (time / knownDuration) * 100) : 0

  useEffect(() => {
    const main = mainRef.current
    const overlay = overlayRef.current
    if (!main || !overlay) return

    let raf = 0
    const tick = () => {
      const t = main.currentTime
      const hit =
        showPlaced &&
        placements.find((p) => p.clip_url && t >= p.start_s && t < p.end_s)

      if (!hit || !hit.clip_url) {
        overlay.style.opacity = "0"
        if (!overlay.paused) overlay.pause()
        activeClip.current = null
      } else {
        const local = Math.max(0, t - hit.start_s)
        if (activeClip.current !== hit.clip_url) {
          activeClip.current = hit.clip_url
          overlay.src = hit.clip_url
        }
        overlay.style.opacity = "1"
        if (Math.abs(overlay.currentTime - local) > 0.3) {
          try {
            overlay.currentTime = local
          } catch {
            /* seek race */
          }
        }
        if (main.paused) {
          if (!overlay.paused) overlay.pause()
        } else if (overlay.paused) {
          overlay.play().catch(() => {})
        }
      }
      raf = window.requestAnimationFrame(tick)
    }
    raf = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(raf)
  }, [placements, showPlaced])

  useEffect(() => {
    const main = mainRef.current
    if (!main) return
    const sync = () => {
      setTime(main.currentTime || 0)
      setPaused(main.paused)
      setMuted(main.muted)
      const next = main.duration
      if (Number.isFinite(next) && next > 0) setDuration(next)
    }
    sync()
    main.addEventListener("timeupdate", sync)
    main.addEventListener("loadedmetadata", sync)
    main.addEventListener("durationchange", sync)
    main.addEventListener("play", sync)
    main.addEventListener("pause", sync)
    main.addEventListener("volumechange", sync)
    return () => {
      main.removeEventListener("timeupdate", sync)
      main.removeEventListener("loadedmetadata", sync)
      main.removeEventListener("durationchange", sync)
      main.removeEventListener("play", sync)
      main.removeEventListener("pause", sync)
      main.removeEventListener("volumechange", sync)
    }
  }, [videoUrl])

  function playheadDuration() {
    const main = mainRef.current
    const media = main && Number.isFinite(main.duration) ? main.duration : 0
    if (media > 0) return media
    return knownDuration
  }

  function seekRatio(clientX: number) {
    const bar = barRef.current
    const main = mainRef.current
    const dur = playheadDuration()
    if (!bar || !main || !(dur > 0)) return
    const rect = bar.getBoundingClientRect()
    const ratio = rect.width > 0 ? (clientX - rect.left) / rect.width : 0
    main.currentTime = Math.min(dur, Math.max(0, ratio * dur))
  }

  function togglePlay() {
    const main = mainRef.current
    if (!main) return
    if (main.paused) main.play().catch(() => {})
    else main.pause()
  }

  function toggleMute() {
    const main = mainRef.current
    if (!main) return
    main.muted = !main.muted
    setMuted(main.muted)
  }

  function toggleFullscreen() {
    const shell = shellRef.current
    if (!shell) return
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {})
    } else {
      shell.requestFullscreen().catch(() => {})
    }
  }

  return (
    <div
      ref={shellRef}
      className="group/player relative overflow-hidden rounded-md border border-border bg-black shadow-soft [&:fullscreen]:h-screen [&:fullscreen]:rounded-none"
    >
      <div className="relative aspect-video bg-black group-[:fullscreen]/player:aspect-auto group-[:fullscreen]/player:h-full">
        <video
          ref={mainRef}
          src={videoUrl}
          poster={posterUrl || undefined}
          playsInline
          preload="metadata"
          className="absolute inset-0 h-full w-full object-contain"
          onClick={togglePlay}
        />
        <video
          ref={overlayRef}
          className="pointer-events-none absolute inset-0 h-full w-full object-contain opacity-0 transition-opacity duration-150"
          muted
          playsInline
          preload="auto"
        />
        {paused ? (
          <button
            type="button"
            className="absolute left-1/2 top-1/2 z-[3] grid size-14 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/15 bg-black/70 text-white"
            aria-label="Play"
            onClick={togglePlay}
          >
            <PlayIcon className="ml-0.5 size-6" />
          </button>
        ) : null}
      </div>

      {readyCount > 0 ? (
        <button
          type="button"
          className={cn(
            "absolute bottom-14 right-3.5 z-[6] inline-flex items-center gap-2 rounded-full border border-white/18 bg-black/78 px-3 py-2 text-[12.5px] font-medium text-white backdrop-blur-sm transition-colors duration-150",
            "hover:bg-black/92",
            showPlaced && "border-accent/55"
          )}
          aria-pressed={showPlaced}
          aria-label={
            showPlaced
              ? "Placement overlays on — click to show original"
              : "Showing original — click to enable placements"
          }
          onClick={() => setShowPlaced((v) => !v)}
        >
          <span
            className={cn(
              "size-2 rounded-full bg-faint",
              showPlaced && "bg-accent shadow-[0_0_0_3px_rgba(229,9,20,0.28)]"
            )}
            aria-hidden
          />
          {showPlaced ? "Placements on" : "Original"}
        </button>
      ) : null}

      <div className="absolute inset-x-0 bottom-0 z-[5] bg-gradient-to-t from-black/80 to-transparent pt-8">
        <div className="flex items-center gap-2 px-3 pb-1.5 text-[12px] text-white/85">
          <button
            type="button"
            className="grid size-7 place-items-center rounded-full hover:bg-white/10"
            aria-label={paused ? "Play" : "Pause"}
            onClick={togglePlay}
          >
            {paused ? <PlayIcon className="ml-0.5 size-3.5" /> : <PauseIcon className="size-3.5" />}
          </button>
          <span className="font-mono text-[11px] tabular-nums text-white/70">
            {formatClock(time)} / {formatClock(knownDuration)}
          </span>
          <span className="flex-1" />
          <button
            type="button"
            className="grid size-7 place-items-center rounded-full hover:bg-white/10"
            aria-label={muted ? "Unmute" : "Mute"}
            onClick={toggleMute}
          >
            {muted ? <MuteIcon className="size-3.5" /> : <VolumeIcon className="size-3.5" />}
          </button>
          <button
            type="button"
            className="grid size-7 place-items-center rounded-full hover:bg-white/10"
            aria-label="Fullscreen"
            onClick={toggleFullscreen}
          >
            <FullscreenIcon className="size-3.5" />
          </button>
        </div>
        <div
          ref={barRef}
          className="relative flex h-3 cursor-pointer items-center"
          role="slider"
          aria-label="Video progress"
          aria-valuemin={0}
          aria-valuemax={knownDuration || 0}
          aria-valuenow={time}
          tabIndex={0}
          onPointerDown={(event) => {
            const target = event.target
            if (target instanceof Element && target.closest("button")) return
            seekRatio(event.clientX)
          }}
          onKeyDown={(event) => {
            const main = mainRef.current
            const dur = playheadDuration()
            if (!main || !(dur > 0)) return
            if (event.key === "ArrowRight") {
              main.currentTime = Math.min(dur, main.currentTime + 5)
            } else if (event.key === "ArrowLeft") {
              main.currentTime = Math.max(0, main.currentTime - 5)
            }
          }}
        >
          <div className="pointer-events-none relative h-1 w-full bg-white/20">
            <div
              className="absolute inset-y-0 left-0 bg-white/80"
              style={{ width: `${playedPct}%` }}
            />
          </div>
          {ticks.map((tick) => (
            <button
              key={tick.id}
              type="button"
              aria-label={tick.brand ? `${tick.brand} placement` : "Placement"}
              className="group/tick absolute top-1/2 z-[1] h-4 -translate-y-1/2 border-0 bg-transparent p-0"
              style={{
                left: tick.spanned ? `${tick.leftPct}%` : `calc(${tick.leftPct}% - 6px)`,
                width: tick.spanned ? `${tick.widthPct}%` : 14,
              }}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation()
                const main = mainRef.current
                const dur = playheadDuration()
                if (!main || !(dur > 0)) return
                const start = (tick.leftPct / 100) * dur
                main.currentTime = Math.min(dur, Math.max(0, start))
              }}
            >
              <span
                className={cn(
                  "pointer-events-none absolute top-1/2 h-[3px] -translate-y-1/2 rounded-[1px] bg-accent shadow-[0_0_0_1px_rgba(0,0,0,0.45)]",
                  tick.spanned ? "inset-x-0" : "left-1/2 w-0.5 -translate-x-1/2"
                )}
              />
              {tick.brand ? (
                <span className="pointer-events-none absolute bottom-[calc(100%+6px)] left-1/2 z-10 hidden -translate-x-1/2 whitespace-nowrap rounded-sm border border-white/15 bg-black/85 px-1.5 py-0.5 text-[10px] font-medium text-white group-hover/tick:block group-focus-visible/tick:block">
                  {tick.brand}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function PlayIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 12 12" className={className} aria-hidden>
      <path d="M2.5 1.2v9.6L10.8 6 2.5 1.2z" fill="currentColor" />
    </svg>
  )
}

function PauseIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 12 12" className={className} aria-hidden>
      <path d="M2 1.5h2.6v9H2v-9zm5.4 0H10v9H7.4v-9z" fill="currentColor" />
    </svg>
  )
}

function VolumeIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path
        d="M2 6h2.2L8 3.2v9.6L4.2 10H2V6zm8.2 1.1a2.4 2.4 0 0 1 0 1.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path d="M3 6.2h1.6L8 3.6v8.8L4.6 9.8H3V6.2z" fill="currentColor" />
    </svg>
  )
}

function MuteIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path d="M3 6.2h1.6L8 3.6v8.8L4.6 9.8H3V6.2z" fill="currentColor" />
      <path
        d="M10 6.2 13.6 9.8M13.6 6.2 10 9.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  )
}

function FullscreenIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden>
      <path
        d="M3 6V3.5h2.5M10.5 3.5H13V6M13 10v2.5h-2.5M5.5 12.5H3V10"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
