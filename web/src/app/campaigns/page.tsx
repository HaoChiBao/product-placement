"use client"

import Link from "next/link"
import { useEffect, useState } from "react"

import { AnimatedGroup } from "@/components/motion-primitives/animated-group"
import { TextEffect } from "@/components/motion-primitives/text-effect"
import { TransitionPanel } from "@/components/motion-primitives/transition-panel"
import { TopBar } from "@/components/top-bar"
import { Badge } from "@/components/ui/badge"
import { ButtonLink } from "@/components/ui/button"
import { EmptyState, Skeleton } from "@/components/ui/empty-state"
import { PageHeader, PageShell } from "@/components/ui/page-shell"
import { VideoThumb, videoLabel } from "@/components/video-thumb"
import {
  listCampaigns,
  listVideos,
  session,
  type Campaign,
  type Video,
} from "@/lib/catalog-client"
import { cn } from "@/lib/utils"

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

const TABS: [Tab, string][] = [
  ["drafts", "Drafts"],
  ["review", "In progress"],
  ["live", "Live"],
]

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

  const tabIndex = TABS.findIndex(([k]) => k === tab)
  const newHref = hasBrand ? "/videos" : "/"

  return (
    <>
      <TopBar nav="campaigns" />
      <PageShell>
        <PageHeader
          eyebrow="Campaigns"
          title={
            <TextEffect
              as="h1"
              preset="fade-in-blur"
              per="word"
              className="font-display text-[clamp(26px,3.2vw,36px)] font-bold leading-[1.1] tracking-tight"
            >
              Your placements
            </TextEffect>
          }
          action={
            <ButtonLink href={newHref} variant="primary">
              + New placement
            </ButtonLink>
          }
        />

        <div className="mb-[22px] flex gap-1 border-b border-border" role="tablist">
          {TABS.map(([k, l]) => (
            <button
              key={k}
              role="tab"
              type="button"
              aria-selected={tab === k}
              onClick={() => setTab(k)}
              className={cn(
                "-mb-px border-b-2 border-transparent px-3.5 py-2.5 font-medium text-muted-foreground transition-colors duration-150",
                tab === k && "border-foreground text-foreground"
              )}
            >
              {l}{" "}
              <span className="ml-1 font-mono text-[11.5px] text-faint">{tiles[k].length}</span>
            </button>
          ))}
        </div>

        {error ? (
          <p className="mb-4 text-[13px] text-destructive">Couldn’t load campaigns: {error}</p>
        ) : null}

        {loading ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-x-4 gap-y-[22px]">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <div className="thumb" />
                <Skeleton className="w-[70%]" />
                <Skeleton className="w-[40%]" />
              </div>
            ))}
          </div>
        ) : (
          <TransitionPanel
            activeIndex={tabIndex}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            variants={{
              enter: { opacity: 0, y: 8, filter: "blur(4px)" },
              center: { opacity: 1, y: 0, filter: "blur(0px)" },
              exit: { opacity: 0, y: -6, filter: "blur(4px)" },
            }}
          >
            {TABS.map(([k]) => (
              <div key={k}>
                {!tiles[k].length && k !== "drafts" ? (
                  <EmptyState
                    title={
                      k === "review" ? "Nothing generating right now." : "No live placements yet."
                    }
                    description="Approve placements on a video and they’ll show up here."
                    className="mb-6"
                  />
                ) : null}
                <AnimatedGroup
                  preset="blur-slide"
                  className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-x-4 gap-y-[22px]"
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
                  {tiles[k].map((x) => (
                    <Link
                      key={x.campaign.id}
                      href={`/review/${x.video.title_id}/${x.video.id}`}
                      className="flex min-w-0 flex-col gap-1.5 text-left [&:hover_.thumb]:outline [&:hover_.thumb]:outline-2 [&:hover_.thumb]:outline-offset-2 [&:hover_.thumb]:outline-accent"
                    >
                      <VideoThumb v={x.video}>
                        <Badge
                          variant={x.live ? "live" : "overlay"}
                          className="absolute left-2 top-2"
                        >
                          {x.badge}
                        </Badge>
                      </VideoThumb>
                      <div className="line-clamp-2 text-[13px] font-semibold leading-snug">
                        {videoLabel(x.video)}
                      </div>
                      <div className="mt-0.5 text-[12.5px] text-muted-foreground">{x.sub}</div>
                    </Link>
                  ))}
                  {k === "drafts" ? (
                    <Link href={newHref} className="flex min-w-0 flex-col gap-1.5 text-left">
                      <div className="thumb grid place-items-center border-[1.5px] border-dashed border-border bg-muted text-[28px] text-muted-foreground">
                        <span>+</span>
                      </div>
                      <div className="text-[13px] font-semibold leading-snug">New placement</div>
                      <div className="mt-0.5 text-[12.5px] text-muted-foreground">
                        Pick a creator video
                      </div>
                    </Link>
                  ) : null}
                </AnimatedGroup>
              </div>
            ))}
          </TransitionPanel>
        )}
      </PageShell>
    </>
  )
}
