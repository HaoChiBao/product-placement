"use client"

import { useSearchParams } from "next/navigation"
import { Suspense, useEffect, useMemo, useRef, useState } from "react"

import { TextEffect } from "@/components/motion-primitives/text-effect"
import { TextShimmer } from "@/components/motion-primitives/text-shimmer"
import { BrandMoodboard } from "@/components/brand-moodboard"
import { TopBar } from "@/components/top-bar"
import { Button, ButtonLink } from "@/components/ui/button"
import { RollingNumber } from "@/components/ui/rolling-number"
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
  const current = steps[Math.min(stepIdx, steps.length - 1)]

  // Preview of the uploaded product image for the board.
  const [productImage, setProductImage] = useState<string | null>(null)
  useEffect(() => {
    const file = asset.current
    if (!file) return
    const url = URL.createObjectURL(file)
    setProductImage(url)
    return () => URL.revokeObjectURL(url)
  }, [attempt])

  return (
    <PageShell narrow>
      <div className="mx-auto mt-[2vh] flex flex-col gap-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="eyebrow">{done ? "Profile ready" : "Researching"}</div>
            <TextEffect
              as="h1"
              preset="fade-in-blur"
              per="word"
              className="mt-2 font-display text-[clamp(26px,3.2vw,36px)] font-bold leading-[1.1] tracking-tight"
              key={done ? "done" : "run"}
            >
              {done ? `Here’s ${input.business}, on one board` : `Getting to know ${input.business}`}
            </TextEffect>
            <p className="mt-2 max-w-[60ch] text-muted-foreground">
              {done
                ? "This is how we’ll keep every placement on-brand. Happy with it? Pick a video next."
                : "We’re reading your site and socials and pinning what we find. This usually takes under a minute."}
            </p>
          </div>
          {done ? (
            <div className="flex gap-2.5">
              <ButtonLink href="/" variant="secondary">
                Edit
              </ButtonLink>
              <ButtonLink href="/videos" variant="primary" size="lg">
                Browse creator videos →
              </ButtonLink>
            </div>
          ) : null}
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3 text-[13px]">
            {error ? (
              <span className="flex flex-wrap items-center gap-2.5 text-destructive">
                {error}
                <Button size="sm" onClick={() => setAttempt((a) => a + 1)}>
                  Try again
                </Button>
              </span>
            ) : done ? (
              <span className="text-success-foreground">Brand profile saved</span>
            ) : (
              <TextShimmer className="font-medium" duration={1.4}>
                {current ? `${current.label}…` : "Starting…"}
              </TextShimmer>
            )}
            <span className="flex items-baseline gap-1.5 text-muted-foreground">
              <span className="font-medium tabular-nums text-foreground">
                <RollingNumber value={sources} />
              </span>
              pages read
            </span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-muted">
            <i
              className="block h-full bg-accent transition-[width] duration-500 ease-out"
              style={{ width: `${done ? 100 : pct}%` }}
            />
          </div>
        </div>

        <BrandMoodboard
          product={input.product}
          productImage={productImage}
          sources={allSources.slice(0, sources)}
          profile={profile}
        />

        {profile ? (
          <details className="text-[12.5px] text-muted-foreground">
            <summary className="cursor-pointer">Brief sent to backend</summary>
            <div className="mt-2 max-h-40 overflow-x-auto whitespace-pre rounded-sm bg-muted px-3 py-2.5 font-mono text-[11.5px] leading-normal">
              {briefFor(profile, input.note)}
            </div>
          </details>
        ) : null}
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
