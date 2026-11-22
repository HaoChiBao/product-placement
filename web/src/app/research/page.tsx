"use client"

import { useSearchParams } from "next/navigation"
import { Suspense, useEffect, useMemo, useRef, useState } from "react"

import { AnimatedGroup } from "@/components/motion-primitives/animated-group"
import { TextEffect } from "@/components/motion-primitives/text-effect"
import { TextShimmer } from "@/components/motion-primitives/text-shimmer"
import { TopBar } from "@/components/top-bar"
import { Badge, StatusDot } from "@/components/ui/badge"
import { Button, ButtonLink } from "@/components/ui/button"
import { Panel } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/empty-state"
import { PageShell } from "@/components/ui/page-shell"
import { createBrand, session } from "@/lib/catalog-client"
import { takePendingAsset } from "@/lib/pending-asset"
import { briefFor, research, type BrandProfile, type ResearchInput } from "@/lib/research"
import { cn } from "@/lib/utils"

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

function Research() {
  const params = useSearchParams()
  const input: ResearchInput = useMemo(
    () => ({
      business: params.get("business") || "",
      website: params.get("website") || "",
      product: params.get("product") || "",
      note: params.get("note") || "",
    }),
    [params]
  )
  const steps = useMemo(() => research.steps(input), [input])
  const allSources = useMemo(() => research.sources(input), [input])

  const [stepIdx, setStepIdx] = useState(0)
  const [sources, setSources] = useState(0)
  const [profile, setProfile] = useState<BrandProfile | null>(null)
  const [error, setError] = useState("")
  const [attempt, setAttempt] = useState(0)
  const asset = useRef<File | null | undefined>(undefined)

  useEffect(() => {
    if (asset.current === undefined) asset.current = takePendingAsset()
    let cancelled = false
    ;(async () => {
      setError("")
      setStepIdx(0)
      setSources(0)
      // Research steps are simulated until the TinyFish agent is wired in.
      const perStep = [1, 3, 1, 2, 0]
      let shown = 0
      for (let i = 0; i < steps.length - 1; i++) {
        if (cancelled) return
        setStepIdx(i)
        for (let k = 0; k < perStep[i] && shown < allSources.length; k++) {
          await wait(300)
          shown++
          setSources(shown)
        }
        await wait(i === steps.length - 2 ? 1200 : 700)
      }
      if (cancelled) return
      setSources(allSources.length)
      setStepIdx(steps.length - 1)
      const p = research.profile(input)
      try {
        const brand = await createBrand(p.business, briefFor(p, input.note), asset.current)
        if (cancelled) return
        session.set({ brandId: brand.id, profile: p, hasAsset: Boolean(asset.current) })
        setStepIdx(steps.length)
        setProfile(p)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [attempt, steps, allSources, input])

  const done = Boolean(profile)
  const pct = Math.min(100, (stepIdx / steps.length) * 100)

  return (
    <PageShell narrow>
      <div className="mx-auto mt-[2vh] grid items-start gap-7 lg:grid-cols-[minmax(0,1fr)_minmax(0,440px)] lg:gap-10">
        <div>
          <div className="eyebrow">{done ? "Profile ready" : "Researching"}</div>
          <TextEffect
            as="h1"
            preset="fade-in-blur"
            per="word"
            className="mt-2 font-display text-[clamp(26px,3.2vw,36px)] font-bold leading-[1.1] tracking-tight"
            key={done ? "done" : "run"}
          >
            {done ? `Here’s what we learned about ${input.business}` : `Reading up on ${input.business}`}
          </TextEffect>
          <p className="mt-2 text-muted-foreground">
            {done
              ? "Check it over. We use this to keep every placement on-brand."
              : "A TinyFish web agent is searching and fetching your public pages. This usually takes under a minute."}
          </p>
          <Panel className="mt-[22px] px-[18px] py-4 shadow-none">
            <div className="mb-3.5 h-1 overflow-hidden rounded-full bg-muted">
              <i className="block h-full bg-accent transition-[width] duration-500 ease-out" style={{ width: `${pct}%` }} />
            </div>
            <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
              {steps.map((s, i) => {
                const ok = i < stepIdx
                const wip = i === stepIdx && !error
                return (
                  <li
                    key={s.label}
                    className={cn(
                      "flex items-start gap-2.5 text-[13.5px] text-faint",
                      (ok || wip) && "text-foreground"
                    )}
                  >
                    <i
                      className={cn(
                        "mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border-[1.5px] border-border font-mono text-[10px] not-italic",
                        wip && "animate-spin border-accent border-t-transparent",
                        ok && "border-accent bg-accent text-white"
                      )}
                    >
                      {ok ? "✓" : ""}
                    </i>
                    <span>
                      {s.label}
                      <small className="mt-0.5 block font-mono text-[11.5px] text-muted-foreground">{s.detail}</small>
                    </span>
                  </li>
                )
              })}
            </ul>
            {error ? (
              <div className="mt-3 flex flex-wrap items-center gap-2.5">
                <span className="text-[13px] text-destructive">Couldn’t save the brand profile: {error}</span>
                <Button size="sm" onClick={() => setAttempt((a) => a + 1)}>
                  Try again
                </Button>
              </div>
            ) : null}
          </Panel>
          <Panel className="mt-3.5 px-[18px] py-3.5 shadow-none">
            <div className="mb-2 flex justify-between">
              <span className="eyebrow">Sources fetched</span>
              <span className="font-mono text-muted-foreground">{sources}</span>
            </div>
            <AnimatedGroup preset="fade" className="flex flex-col gap-1.5" as="ul">
              {allSources.slice(0, sources).map((s) => (
                <li key={s.url} className="flex min-w-0 items-center gap-2.5 text-[12.5px]">
                  <span className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10.5px] text-muted-foreground bg-muted">
                    {s.kind}
                  </span>
                  <b className="min-w-0 truncate font-medium">{s.url}</b>
                  <em className="ml-auto shrink-0 font-mono text-[11px] not-italic text-faint">
                    {(s.ms / 1000).toFixed(1)}s
                  </em>
                </li>
              ))}
            </AnimatedGroup>
          </Panel>
        </div>
        <div>
          <Panel className="sticky top-[120px] flex flex-col gap-4 p-5">
            <div className="flex items-center gap-3">
              <div className="grid size-11 shrink-0 place-items-center rounded-md bg-foreground font-display text-lg font-bold text-background">
                {input.business[0] || "·"}
              </div>
              <div className="min-w-0 flex-1">
                <b className="font-semibold">{input.business}</b>
                <div className="mt-0.5 text-[12.5px] text-muted-foreground">
                  {profile ? [profile.website, profile.location].filter(Boolean).join(" · ") : input.website}
                </div>
              </div>
              {done ? (
                <Badge>
                  <StatusDot />
                  Saved
                </Badge>
              ) : (
                <Badge variant="warning">
                  <TextShimmer className="text-[11.5px] font-medium" duration={1.2}>
                    Building…
                  </TextShimmer>
                </Badge>
              )}
            </div>
            {profile ? (
              <>
                <dl className="m-0 grid grid-cols-[96px_1fr] gap-x-3 gap-y-2.5 text-[13px]">
                  <dt className="text-muted-foreground">Product</dt>
                  <dd className="m-0 min-w-0">
                    <b className="font-semibold">{profile.product}</b>
                    <div className="mt-0.5 text-[12.5px] text-muted-foreground">{profile.productFacts}</div>
                  </dd>
                  <dt className="text-muted-foreground">Category</dt>
                  <dd className="m-0">{profile.category}</dd>
                  <dt className="text-muted-foreground">Tone</dt>
                  <dd className="m-0">{profile.tone}</dd>
                  <dt className="text-muted-foreground">Audience</dt>
                  <dd className="m-0">{profile.audience}</dd>
                  <dt className="text-muted-foreground">Colors</dt>
                  <dd className="m-0 flex gap-1.5">
                    {profile.colors.map((c) => (
                      <span key={c} className="size-[22px] rounded-xs border border-border" style={{ background: c }} />
                    ))}
                  </dd>
                  <dt className="text-muted-foreground">Good scenes</dt>
                  <dd className="m-0 flex flex-wrap gap-1">
                    {profile.goodScenes.map((s) => (
                      <span key={s} className="rounded-full border border-border px-2 py-0.5 text-[11.5px] text-muted-foreground">
                        {s}
                      </span>
                    ))}
                  </dd>
                  <dt className="text-muted-foreground">Avoid</dt>
                  <dd className="m-0 flex flex-wrap gap-1">
                    {profile.avoidScenes.map((s) => (
                      <span
                        key={s}
                        className="rounded-full border border-border px-2 py-0.5 text-[11.5px] text-faint line-through"
                      >
                        {s}
                      </span>
                    ))}
                  </dd>
                </dl>
                <details>
                  <summary className="cursor-pointer text-[12.5px] text-muted-foreground">Brief sent to backend</summary>
                  <div className="mt-2 max-h-40 overflow-x-auto whitespace-pre rounded-sm bg-muted px-3 py-2.5 font-mono text-[11.5px] leading-normal text-muted-foreground">
                    {briefFor(profile, input.note)}
                  </div>
                </details>
                <div className="flex gap-2.5">
                  <ButtonLink href="/" variant="secondary">
                    Edit
                  </ButtonLink>
                  <ButtonLink href="/videos" variant="primary" size="lg" className="flex-1">
                    Browse creator videos →
                  </ButtonLink>
                </div>
              </>
            ) : (
              <>
                <div className="flex flex-col gap-2.5">
                  {[90, 70, 80, 55, 75, 60].map((w, i) => (
                    <Skeleton key={i} style={{ width: `${w}%` } as React.CSSProperties} />
                  ))}
                </div>
                <p className="text-xs leading-snug text-muted-foreground">Your profile fills in as sources come back.</p>
              </>
            )}
          </Panel>
        </div>
      </div>
    </PageShell>
  )
}

export default function ResearchPage() {
  return (
    <>
      <TopBar step="research" />
      <Suspense fallback={null}>
        <Research />
      </Suspense>
    </>
  )
}
