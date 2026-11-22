"use client"

import { useSearchParams } from "next/navigation"
import { Suspense, useEffect, useMemo, useRef, useState } from "react"

import { TextEffect } from "@/components/motion-primitives/text-effect"
import { TextShimmer } from "@/components/motion-primitives/text-shimmer"
import { TopBar } from "@/components/top-bar"
import { Badge, StatusDot } from "@/components/ui/badge"
import { Button, ButtonLink } from "@/components/ui/button"
import { Panel } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/empty-state"
import { RollingNumber } from "@/components/ui/rolling-number"
import {
  TerminalAnimationBlinkingCursor,
  TerminalAnimationCommandBar,
  TerminalAnimationContent,
  TerminalAnimationOutput,
  TerminalAnimationRoot,
  TerminalAnimationWindow,
  type TabContent,
} from "@/components/ui/terminal-animation"
import { PageShell } from "@/components/ui/page-shell"
import { createBrand, session } from "@/lib/catalog-client"
import { takePendingAsset } from "@/lib/pending-asset"
import {
  briefFor,
  forgetResearch,
  pollResearch,
  research,
  startResearch,
  type BrandProfile,
  type ResearchInput,
  type ResearchSource,
  type ResearchStep,
} from "@/lib/research"

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
  const forceMock = params.get("mock") === "1"
  const mockSteps = useMemo(() => research.steps(input), [input])
  const mockSources = useMemo(() => research.sources(input), [input])
  // Live TinyFish job state; null while simulating (or before the job reports).
  const [live, setLive] = useState<{ steps: ResearchStep[]; sources: ResearchSource[] } | null>(null)
  const steps = live ? live.steps : mockSteps
  const allSources = live ? live.sources : mockSources

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
      let p: BrandProfile
      let n = mockSteps.length
      let started: { jobId: string } | { mock: true } = { mock: true }
      if (!forceMock) {
        setLive({ steps: [], sources: [] })
        try {
          started = await startResearch(input, attempt)
        } catch (e) {
          if (!cancelled) setError(e instanceof Error ? e.message : String(e))
          return
        }
        if (cancelled) return
      }
      if ("jobId" in started) {
        let job
        try {
          job = await pollResearch(
            started.jobId,
            (j) => {
              setLive({ steps: j.steps || [], sources: j.sources || [] })
              setStepIdx(j.step || 0)
              setSources((j.sources || []).length)
            },
            () => cancelled
          )
        } catch (e) {
          if (!cancelled) {
            forgetResearch(input, attempt)
            setError(e instanceof Error ? e.message : String(e))
          }
          return
        }
        if (!job || cancelled) return
        forgetResearch(input, attempt)
        p = job.profile!
        n = job.steps.length
        setStepIdx(n - 1)
      } else {
        // No TinyFish key on the server (or ?mock=1): simulate the research steps.
        setLive(null)
        const perStep = [1, 3, 1, 2, 0]
        let shown = 0
        for (let i = 0; i < mockSteps.length - 1; i++) {
          if (cancelled) return
          setStepIdx(i)
          for (let k = 0; k < perStep[i] && shown < mockSources.length; k++) {
            await wait(300)
            shown++
            setSources(shown)
          }
          await wait(i === mockSteps.length - 2 ? 1200 : 700)
        }
        if (cancelled) return
        setSources(mockSources.length)
        setStepIdx(mockSteps.length - 1)
        p = research.profile(input)
      }
      try {
        const brand = await createBrand(p.business, briefFor(p, input.note), asset.current)
        if (cancelled) return
        session.set({ brandId: brand.id, profile: p, hasAsset: Boolean(asset.current) })
        setStepIdx(n)
        setProfile(p)
      } catch (e) {
        if (!cancelled) setError(`Couldn’t save the brand profile: ${e instanceof Error ? e.message : String(e)}`)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [attempt, forceMock, mockSteps, mockSources, input])

  const done = Boolean(profile)
  const pct = steps.length ? Math.min(100, (stepIdx / steps.length) * 100) : 0

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
          <ResearchTerminal
            input={input}
            pct={pct}
            sources={sources}
            done={done}
            error={error}
            onRetry={() => setAttempt((a) => a + 1)}
          />
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

/** TinyFish agent log, replayed as a terminal session alongside the research steps. */
function ResearchTerminal({
  input,
  pct,
  sources,
  done,
  error,
  onRetry,
}: {
  input: ResearchInput
  pct: number
  sources: number
  done: boolean
  error: string
  onRetry: () => void
}) {
  const tabs: TabContent[] = useMemo(() => {
    const steps = research.steps(input)
    const all = research.sources(input)
    const perStep = [1, 3, 1, 2, 0, 0]
    let k = 0
    const lines: TabContent["lines"] = []
    steps.forEach((s, i) => {
      lines.push({ text: `→ ${s.label}`, color: "text-white", delay: i ? 520 : 200 })
      lines.push({ text: `  ${s.detail}`, color: "text-neutral-500", delay: 120 })
      for (let n = 0; n < perStep[i] && k < all.length; n++, k++) {
        const src = all[k]
        lines.push({
          text: `  ${src.kind.padEnd(6)} ${src.url}  ${(src.ms / 1000).toFixed(1)}s`,
          color: src.kind === "search" ? "text-sky-300" : "text-emerald-300",
          delay: 300,
        })
      }
    })
    const site = input.website || input.business
    return [{ label: "research", command: `tinyfish research ${site}`, lines }]
  }, [input])

  return (
    <div className="mt-[22px] overflow-hidden rounded-lg border border-stage-line bg-stage shadow-soft">
      <div className="flex items-center gap-2 border-b border-stage-line px-4 py-2.5">
        <span className="size-2.5 rounded-full bg-[#ff5f57]" />
        <span className="size-2.5 rounded-full bg-[#febc2e]" />
        <span className="size-2.5 rounded-full bg-[#28c840]" />
        <span className="ml-2 font-mono text-[11.5px] text-stage-muted">tinyfish · web agent</span>
        <span className="ml-auto flex items-baseline gap-1.5 font-mono text-[11.5px] text-stage-muted">
          sources
          <span className="text-stage-ink tabular-nums">
            <RollingNumber value={sources} />
          </span>
        </span>
      </div>
      <div className="h-0.5 bg-stage-2">
        <i className="block h-full bg-accent transition-[width] duration-500 ease-out" style={{ width: `${pct}%` }} />
      </div>
      <TerminalAnimationRoot tabs={tabs} alwaysDark hideCursorOnComplete={false}>
        <TerminalAnimationWindow backgroundColor="var(--stage)" minHeight="19rem" animateOnVisible={false} className="rounded-none">
          <TerminalAnimationContent className="px-5 py-4 font-mono text-[12.5px] leading-[1.7] sm:px-5 sm:py-4">
            <TerminalAnimationCommandBar
              className="text-stage-ink"
              cursor={<TerminalAnimationBlinkingCursor className="bg-accent" />}
            />
            <TerminalAnimationOutput
              className="mt-1 whitespace-pre-wrap break-all"
              renderLine={(line, _i, visible) =>
                visible ? <span className={line.color}>{line.text || " "}</span> : null
              }
            />
            {done ? <div className="mt-1 text-accent">✓ brand profile saved</div> : null}
            {error ? (
              <div className="mt-2 flex flex-wrap items-center gap-2.5">
                <span className="text-[#f2555a]">✗ couldn’t save the brand profile: {error}</span>
                <Button size="sm" variant="stage" onClick={onRetry}>
                  Try again
                </Button>
              </div>
            ) : null}
          </TerminalAnimationContent>
        </TerminalAnimationWindow>
      </TerminalAnimationRoot>
    </div>
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
