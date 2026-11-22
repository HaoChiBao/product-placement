"use client"

import { useMemo, useRef, useState } from "react"

import { HaloBadge } from "@/components/ui/halo-badge"
import { HaloButton } from "@/components/ui/halo-button"
import {
  TextureCard,
  TextureCardContent,
  TextureCardDescription,
  TextureCardHeader,
  TextureCardTitle,
} from "@/components/ui/texture-card"
import type { Moment } from "@/lib/types"

function formatWindow(start: number, end: number) {
  const fmt = (t: number) => {
    const m = Math.floor(t / 60)
    const s = t - m * 60
    return `${m}:${s.toFixed(1).padStart(4, "0")}`
  }
  return `${fmt(start)} – ${fmt(end)}`
}

export function MomentCard({
  moment,
  index,
  selected,
  onSelect,
}: {
  moment: Moment
  index: number
  selected: boolean
  onSelect: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)

  const frameW = moment.frame_w || 16
  const frameH = moment.frame_h || 9
  const frameAR = frameW / frameH
  const containerAR = 16 / 9

  // Position the box in frame pixels, then place the frame inside a letterboxed
  // wrapper so object-contain padding does not shift the overlay.
  const box = useMemo(
    () => ({
      left: `${(moment.region.x / frameW) * 100}%`,
      top: `${(moment.region.y / frameH) * 100}%`,
      width: `${(moment.region.w / frameW) * 100}%`,
      height: `${(moment.region.h / frameH) * 100}%`,
    }),
    [moment.region, frameW, frameH]
  )

  const mediaStyle = useMemo(() => {
    // Match CSS object-contain: fill one axis, shrink the other.
    if (frameAR > containerAR) {
      return { width: "100%", height: "auto", aspectRatio: `${frameW} / ${frameH}` }
    }
    return { height: "100%", width: "auto", aspectRatio: `${frameW} / ${frameH}` }
  }, [frameAR, frameW, frameH])

  async function togglePlay() {
    onSelect()
    const el = videoRef.current
    if (!el) return
    if (el.paused) {
      try {
        await el.play()
        setPlaying(true)
      } catch {
        setPlaying(false)
      }
    } else {
      el.pause()
      setPlaying(false)
    }
  }

  return (
    <TextureCard
      className={`overflow-hidden transition-[box-shadow,transform] ${
        selected ? "ring-2 ring-[var(--place)] shadow-lg" : "hover:-translate-y-0.5"
      }`}
    >
      <button type="button" className="w-full text-left" onClick={onSelect}>
        <div className="relative flex aspect-video w-full items-center justify-center overflow-hidden bg-neutral-900">
          <div className="relative max-h-full max-w-full" style={mediaStyle}>
            <img
              src={moment.frameUrl}
              alt={`Moment ${index + 1} frame`}
              className="h-full w-full object-fill"
            />
            <div
              className="pointer-events-none absolute border-2 border-[var(--place)] shadow-[0_0_0_1px_rgba(0,0,0,0.35)]"
              style={box}
            />
          </div>
          <div className="absolute left-3 top-3 flex flex-wrap gap-2">
            <HaloBadge variant="secondary">{`#${index + 1}`}</HaloBadge>
            <HaloBadge variant="default">{moment.score.toFixed(2)}</HaloBadge>
            {moment.brand_fit && (
              <HaloBadge variant="secondary">
                fit {(moment.brand_fit.total ?? 0).toFixed(2)}
              </HaloBadge>
            )}
          </div>
        </div>
      </button>

      <TextureCardHeader className="px-4 pb-1 pt-4">
        <TextureCardTitle className="font-[family-name:var(--font-display)] text-lg text-foreground">
          {moment.whats_there}
        </TextureCardTitle>
        <TextureCardDescription className="font-mono text-xs tracking-wide text-muted-foreground">
          {formatWindow(moment.start_s, moment.end_s)} · {moment.end_s - moment.start_s}
          s window
        </TextureCardDescription>
      </TextureCardHeader>

      <TextureCardContent className="space-y-3 px-4 pb-4">
        <p className="text-sm leading-relaxed text-foreground/80">{moment.reason}</p>
        {moment.brand_fit?.reason && (
          <p className="rounded-xl bg-[var(--accent)]/50 px-3 py-2 text-xs leading-relaxed text-[var(--ink)]">
            Brand fit: {moment.brand_fit.reason}
          </p>
        )}

        <video
          ref={videoRef}
          className="aspect-video w-full rounded-md bg-black object-contain"
          src={moment.clipUrl}
          controls
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
        />

        <HaloButton
          type="button"
          className="w-full"
          onClick={togglePlay}
          loadingText="Loading clip…"
        >
          {playing ? "Pause clip" : "Play 5s clip"}
        </HaloButton>
      </TextureCardContent>
    </TextureCard>
  )
}
