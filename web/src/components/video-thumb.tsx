import { mediaUrl, type Video } from "@/lib/catalog-client"

export const videoLabel = (v: Video) =>
  v.title.kind === "series" ? `${v.title.name} · S${v.season}E${v.episode_number}` : v.title.name

export function VideoThumb({ v, children }: { v: Video; children?: React.ReactNode }) {
  const poster = mediaUrl(v.poster_key || v.title.poster_key)
  return (
    <div className="thumb">
      {poster ? <img src={poster} alt="" /> : <div className="thumb-ph" />}
      {children}
    </div>
  )
}
