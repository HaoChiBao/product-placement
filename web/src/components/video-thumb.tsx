"use client"

import { HoverVideoPlayer } from "@/components/ui/hover-video-player"
import { mediaUrl, type Video } from "@/lib/catalog-client"

export const videoLabel = (v: Video) =>
  v.title.kind === "series" ? `${v.title.name} · S${v.season}E${v.episode_number}` : v.title.name

/** Poster thumbnail; with `preview`, hovering plays the episode muted in place. */
export function VideoThumb({
  v,
  preview,
  children,
}: {
  v: Video
  preview?: boolean
  children?: React.ReactNode
}) {
  const poster = mediaUrl(v.poster_key || v.title.poster_key)
  const src = preview ? mediaUrl(v.video_key) : null
  return (
    <div className="thumb">
      {src ? (
        <HoverVideoPlayer
          videoSrc={src}
          thumbnailSrc={poster || undefined}
          muted
          loop
          preload="none"
          playbackStartDelay={150}
          className="absolute inset-0 [&_video]:h-full [&_video]:w-full [&_video]:object-cover"
        />
      ) : poster ? (
        <img src={poster} alt="" />
      ) : (
        <div className="thumb-ph" />
      )}
      {children}
    </div>
  )
}
