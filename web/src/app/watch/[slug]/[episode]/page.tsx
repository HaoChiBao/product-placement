import { Suspense } from "react"

import { WatchClient } from "./watch-client"

export default async function WatchEpisodePage({
  params,
}: {
  params: Promise<{ slug: string; episode: string }>
}) {
  const { slug, episode: raw } = await params
  const [s, e] = String(raw || "1-1").split("-")
  const season = Number(s || 1)
  const episode = Number(e || 1)

  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-black p-6 text-sm text-zinc-500">
          Loading…
        </div>
      }
    >
      <WatchClient slug={slug} season={season} episode={episode} />
    </Suspense>
  )
}
