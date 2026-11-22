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
}

/**
 * Overlay approach: play the source episode, and during each ready placement
 * window swap in a synced overlay <video>. Chosen over full-episode pre-stitch
 * because placements are per-brand; stitching would need one rendition per brand.
 */
export function PlacementPlayer({ videoUrl, posterUrl, placements }: Props) {
  const mainRef = useRef<HTMLVideoElement>(null)
  const overlayRef = useRef<HTMLVideoElement>(null)
  const [showPlaced, setShowPlaced] = useState(true)
  const activeClip = useRef<string | null>(null)

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

  return (
    <div className="relative w-full bg-black">
      <div className="relative aspect-video w-full overflow-hidden bg-black">
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
      </div>

      <button
        type="button"
        aria-label={showPlaced ? "Show original" : "Show placements"}
        title={showPlaced ? "Showing placements — click for original" : "Showing original"}
        className="absolute bottom-14 right-4 h-2 w-2 rounded-full bg-white/25 opacity-40 transition hover:opacity-90"
        onClick={() => setShowPlaced((v) => !v)}
      />
    </div>
  )
}
