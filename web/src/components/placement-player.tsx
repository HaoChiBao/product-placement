"use client"

import { useEffect, useRef, useState } from "react"

export type PlacementWindow = {
  id: string
  start_s: number
  end_s: number
  clip_url: string | null
}

type Props = {
  videoUrl: string
  posterUrl?: string | null
  placements: PlacementWindow[]
  title?: string
}

/**
 * Overlay approach: play the source episode, and during each ready placement
 * window swap in a synced overlay <video>. Chosen over full-episode pre-stitch
 * because placements are per-brand; stitching would need one rendition per brand.
 */
export function PlacementPlayer({
  videoUrl,
  posterUrl,
  placements,
  title,
}: Props) {
  const mainRef = useRef<HTMLVideoElement>(null)
  const overlayRef = useRef<HTMLVideoElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const [showPlaced, setShowPlaced] = useState(true)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [showDebug, setShowDebug] = useState(false)

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
        setActiveId(null)
      } else {
        const local = Math.max(0, t - hit.start_s)
        if (overlay.dataset.clip !== hit.clip_url) {
          overlay.dataset.clip = hit.clip_url
          overlay.src = hit.clip_url
          setActiveId(hit.id)
        } else if (activeId !== hit.id) {
          setActiveId(hit.id)
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
  }, [placements, showPlaced, activeId])

  return (
    <div className="relative w-full bg-black">
      <div
        ref={boxRef}
        className="relative mx-auto aspect-video w-full max-w-[1400px] overflow-hidden bg-black"
      >
        <video
          ref={mainRef}
          className="absolute inset-0 h-full w-full object-contain"
          src={videoUrl}
          poster={posterUrl || undefined}
          controls
          playsInline
          preload="metadata"
        />
        <video
          ref={overlayRef}
          className="pointer-events-none absolute inset-0 h-full w-full object-contain opacity-0 transition-opacity duration-150"
          muted
          playsInline
          preload="auto"
        />
        {title ? (
          <div className="pointer-events-none absolute left-4 top-4 text-sm font-medium text-white/80 drop-shadow">
            {title}
          </div>
        ) : null}
      </div>

      <button
        type="button"
        aria-label="Toggle compare"
        className="absolute bottom-3 right-3 rounded bg-black/50 px-2 py-1 text-[10px] uppercase tracking-wide text-white/40 opacity-30 hover:opacity-100"
        onClick={() => setShowDebug((v) => !v)}
      >
        ·
      </button>

      {showDebug ? (
        <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-3 py-2 text-xs text-white/70">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={showPlaced}
              onChange={(e) => setShowPlaced(e.target.checked)}
            />
            Show placements
          </label>
          <span>
            {placements.length} ready · active {activeId || "—"}
          </span>
        </div>
      ) : null}
    </div>
  )
}
