"use client"

import Link from "next/link"
import { useEffect, useState } from "react"

import { TopBar } from "@/components/top-bar"
import { VideoThumb, videoLabel } from "@/components/video-thumb"
import {
  listCampaigns,
  listVideos,
  session,
  type Campaign,
  type Video,
} from "@/lib/catalog-client"

type Tab = "drafts" | "review" | "live"
type Tile = { video: Video; campaign: Campaign; badge: string; sub: string; live?: boolean }

function ago(iso?: string) {
  if (!iso) return ""
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins} min ago`
  const h = Math.round(mins / 60)
  if (h < 24) return `${h} h ago`
  return `${Math.round(h / 24)} d ago`
}

export default function CampaignsPage() {
  const [tab, setTab] = useState<Tab>("drafts")
  const [videos, setVideos] = useState<Record<string, Video>>({})
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [hasBrand, setHasBrand] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    const s = session.get()
    setHasBrand(Boolean(s))
    Promise.all([listVideos(), s ? listCampaigns(s.brandId) : Promise.resolve([])])
      .then(([vs, cs]) => {
        setVideos(Object.fromEntries(vs.map((v) => [v.id, v])))
        setCampaigns(cs)
      })
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false))
  }, [])

  const forStatus = (status: Campaign["status"]): Tile[] =>
    campaigns
      .filter((c) => c.status === status && videos[c.episode_id])
      .map((c) => {
        const video = videos[c.episode_id]
        if (status === "draft") {
          return { video, campaign: c, badge: "Draft", sub: `saved ${ago(c.updated_at)}` }
        }
        if (status === "in_progress") {
          return { video, campaign: c, badge: "Generating", sub: `updated ${ago(c.updated_at)}` }
        }
        return {
          video,
          campaign: c,
          badge: "Live",
          sub: `updated ${ago(c.updated_at)}`,
          live: true,
        }
      })

  const tiles: Record<Tab, Tile[]> = {
    drafts: forStatus("draft"),
    review: forStatus("in_progress"),
    live: forStatus("live"),
  }

  const TABS: [Tab, string][] = [
    ["drafts", "Drafts"],
    ["review", "In progress"],
    ["live", "Live"],
  ]

  return (
    <>
      <TopBar nav="campaigns" />
      <main className="wrap">
        <div className="mk-head">
          <div>
            <div className="eyebrow">Campaigns</div>
            <h1 style={{ marginTop: 6 }}>Your placements</h1>
          </div>
          <Link className="btn btn-primary" href={hasBrand ? "/videos" : "/"}>
            + New placement
          </Link>
        </div>
        <div className="tabs" role="tablist">
          {TABS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
              {l} <span>{tiles[k].length}</span>
            </button>
          ))}
        </div>
        {error ? <p className="error">Couldn’t load campaigns: {error}</p> : null}
        {!loading && !tiles[tab].length && tab !== "drafts" ? (
          <div className="empty">
            <b>{tab === "review" ? "Nothing generating right now." : "No live placements yet."}</b>
            <span>Approve placements on a video and they’ll show up here.</span>
          </div>
        ) : null}
        <div className="dgrid">
          {tiles[tab].map((x) => (
            <Link
              key={x.campaign.id}
              className="dtile"
              href={`/review/${x.video.title_id}/${x.video.id}`}
            >
              <VideoThumb v={x.video}>
                <span className={`badge-tl ${x.live ? "live" : ""}`}>{x.badge}</span>
              </VideoThumb>
              <div className="card-t" style={{ fontSize: 13 }}>
                {videoLabel(x.video)}
              </div>
              <div className="card-s">{x.sub}</div>
            </Link>
          ))}
          {tab === "drafts" ? (
            <Link className="dtile dnew" href={hasBrand ? "/videos" : "/"}>
              <div className="thumb">
                <span>+</span>
              </div>
              <div className="card-t" style={{ fontSize: 13 }}>
                New placement
              </div>
              <div className="card-s">Pick a creator video</div>
            </Link>
          ) : null}
        </div>
      </main>
    </>
  )
}
