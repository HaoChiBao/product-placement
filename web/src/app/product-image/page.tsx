"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useMemo, useRef, useState } from "react"

import { TextEffect } from "@/components/motion-primitives/text-effect"
import { TopBar } from "@/components/top-bar"
import { Badge } from "@/components/ui/badge"
import { Button, ButtonLink } from "@/components/ui/button"
import { Panel } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/empty-state"
import { PageShell } from "@/components/ui/page-shell"
import { createBrand, mediaUrl, session } from "@/lib/catalog-client"
import { getFlow, researchDone, sameImage, sameInput, setFlow, type Flow, type FlowImage } from "@/lib/flow"
import { takePendingAsset } from "@/lib/pending-asset"
import {
  briefFor,
  research,
  type BrandProfile,
  type ResearchImage,
  type ResearchInput,
  type ResearchJob,
} from "@/lib/research"
import { cn } from "@/lib/utils"

/** Shows transparency in a cutout. */
const CHECKER: React.CSSProperties = {
  background: "repeating-conic-gradient(#d4d6d5 0% 25%, #f4f5f4 0% 50%) 50% / 16px 16px",
}

const SOURCE_LABEL: Record<ResearchImage["source"], string> = {
  agent: "Product page",
  shopify: "Shop listing",
  img: "Your site",
  retailer: "Retailer listing",
}

type Choice = { kind: "candidate"; name: string } | { kind: "own" } | null

const toFlowImage = (c: Choice): FlowImage => (c ? c : { kind: "none" })

/** URL of the asset saved with a brand (for showing an earlier upload again). */
async function savedAssetUrl(brandId: string): Promise<string | null> {
  try {
    const res = await fetch("/api/catalog/brands", { cache: "no-store" })
    const body = (await res.json()) as { brands?: { id: string; assets?: { object_key?: string }[] }[] }
    const brand = (body.brands || []).find((b) => b.id === brandId)
    return mediaUrl(brand?.assets?.[0]?.object_key)
  } catch {
    return null
  }
}

function ProductImage() {
  const params = useSearchParams()
  const router = useRouter()
  const input: ResearchInput = useMemo(
    () => ({
      business: params.get("business") || "",
      website: params.get("website") || "",
      product: params.get("product") || "",
      note: params.get("note") || "",
    }),
    [params]
  )
  const jobId = params.get("job") || ""

  const pending = useRef<File | null | undefined>(undefined)
  const fileRef = useRef<HTMLInputElement>(null)
  const [own, setOwn] = useState<File | null>(null)
  const [ownUrl, setOwnUrl] = useState<string | null>(null)
  // Reopened after saving: what was saved, so an unchanged choice isn't saved again.
  const [saved, setSaved] = useState<{ flow: Flow; image?: FlowImage } | null>(null)
  const [savedOwnUrl, setSavedOwnUrl] = useState<string | null>(null)
  const [profile, setProfile] = useState<BrandProfile | null>(null)
  const [images, setImages] = useState<ResearchImage[]>([])
  const [choice, setChoice] = useState<Choice>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!own) return setOwnUrl(null)
    const url = URL.createObjectURL(own)
    setOwnUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [own])

  useEffect(() => {
    // An upload from the first page wins over the research candidates.
    if (pending.current === undefined) pending.current = takePendingAsset()
    const upload = pending.current
    if (upload) setOwn(upload)
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError("")
      let p: BrandProfile | null = null
      let imgs: ResearchImage[] = []
      if (jobId) {
        try {
          const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" })
          const job = (await res.json()) as ResearchJob
          if (res.ok && job.profile) {
            p = job.profile
            imgs = job.images || []
          }
        } catch {
          // handled below
        }
      } else {
        // Simulated research (no TinyFish key): mock profile, no candidates.
        p = research.profile(input)
      }
      if (cancelled) return
      if (!p) {
        setError("We couldn’t load the research results. Run the research again.")
      }
      const flow = getFlow()
      const sameRun =
        flow &&
        researchDone(flow) &&
        (jobId ? flow.jobId === jobId : !flow.jobId && sameInput(flow.input, input))
      const prev = sameRun && flow.brandId ? flow.image : undefined
      setSaved(sameRun ? { flow, image: prev } : null)
      setSavedOwnUrl(prev?.kind === "own" ? prev.assetUrl || null : null)
      setProfile(p)
      setImages(imgs)
      // A new upload wins; else what was saved last time; else the best candidate.
      const savedChoice: Choice | undefined =
        prev?.kind === "candidate" && imgs.some((i) => i.name === prev.name)
          ? { kind: "candidate", name: prev.name }
          : prev?.kind === "own" && prev.assetUrl
            ? { kind: "own" }
            : prev?.kind === "none"
              ? null
              : undefined
      setChoice(
        upload
          ? { kind: "own" }
          : savedChoice !== undefined
            ? savedChoice
            : imgs[0]
              ? { kind: "candidate", name: imgs[0].name }
              : null
      )
      setLoading(false)
    })()
    return () => {
      cancelled = true
    }
  }, [jobId, input])

  function pickOwn(file: File | null) {
    if (!file) return
    setOwn(file)
    setChoice({ kind: "own" })
  }

  // Nothing changed since the brand was saved: just move on, don't save again.
  const unchanged = Boolean(
    saved?.flow.brandId && saved.image && !own && sameImage(toFlowImage(choice), saved.image)
  )

  async function save(withImage: boolean) {
    if (!profile) return
    if (withImage && unchanged) {
      router.push("/videos")
      return
    }
    setSaving(true)
    setError("")
    try {
      let file: File | null = null
      if (withImage && choice?.kind === "own") {
        file = own
        if (!file && savedOwnUrl) {
          // The earlier upload isn't in memory any more; use the copy saved with the brand.
          const res = await fetch(savedOwnUrl)
          if (!res.ok) throw new Error(`Couldn’t load your saved image (${res.status})`)
          const blob = await res.blob()
          file = new File([blob], (saved?.image?.kind === "own" && saved.image.fileName) || "product.png", {
            type: blob.type || "image/png",
          })
        }
      }
      if (withImage && choice?.kind === "candidate") {
        const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/file/${choice.name}`)
        if (!res.ok) throw new Error(`Couldn’t load the product image (${res.status})`)
        const blob = await res.blob()
        file = new File([blob], choice.name, { type: "image/png" })
      }
      const brand = await createBrand(profile.business, briefFor(profile, input.note), file)
      session.set({ brandId: brand.id, profile, hasAsset: Boolean(file) })
      const picked = withImage ? choice : null
      const image: FlowImage =
        picked?.kind === "own"
          ? { kind: "own", assetUrl: own ? await savedAssetUrl(brand.id) : savedOwnUrl, fileName: own?.name }
          : toFlowImage(picked)
      const base = saved?.flow || getFlow() || { input, jobId: jobId || undefined }
      setFlow({ ...base, image, brandId: brand.id })
      router.push("/videos")
    } catch (e) {
      setError(`Couldn’t save the brand profile: ${e instanceof Error ? e.message : String(e)}`)
      setSaving(false)
    }
  }

  const backQuery = new URLSearchParams(Object.entries(input).filter(([, v]) => v) as [string, string][])
  if (jobId) backQuery.set("job", jobId)
  const backHref = `/research?${backQuery}`
  const ownSrc = ownUrl || savedOwnUrl
  const nothing = !loading && !images.length && !ownSrc
  const savedBefore = Boolean(saved?.flow.brandId)

  return (
    <PageShell narrow>
      <div className="mx-auto mt-[2vh] max-w-[760px]">
        <div className="eyebrow">{savedBefore ? "Product image · saved" : "Product image"}</div>
        <TextEffect
          as="h1"
          preset="fade-in-blur"
          per="word"
          className="mt-2 font-display text-[clamp(26px,3.2vw,36px)] font-bold leading-[1.1] tracking-tight"
          key={nothing ? "add" : "confirm"}
        >
          {nothing
            ? `Add an image of your ${input.product || "product"}`
            : `Is this your ${input.product || "product"}?`}
        </TextEffect>
        <p className="mt-2 text-muted-foreground">
          {savedBefore
            ? "This is what you saved with your brand. Pick another image to change it."
            : nothing && !jobId
              ? "This was a simulated research run (no TinyFish key), so we didn’t look for images. Upload one, or continue without it."
              : nothing
                ? "We couldn’t find a clean product image on your site. Upload one, or continue without it."
              : "This is the image we’ll place into creator videos. A transparent cutout blends in best."}
        </p>

        <Panel className="mt-[22px] flex flex-col gap-4 p-5">
          {loading ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="aspect-square h-auto" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {ownSrc ? (
                <Tile
                  selected={choice?.kind === "own"}
                  onClick={() => setChoice({ kind: "own" })}
                  src={ownSrc}
                  label="Your upload"
                  detail={own?.name || (saved?.image?.kind === "own" && saved.image.fileName) || "Saved with your brand"}
                />
              ) : null}
              {images.map((img) => (
                <Tile
                  key={img.name}
                  selected={choice?.kind === "candidate" && choice.name === img.name}
                  onClick={() => setChoice({ kind: "candidate", name: img.name })}
                  src={`/api/jobs/${encodeURIComponent(jobId)}/file/${img.name}`}
                  label={img.host ? `Found on ${img.host}` : SOURCE_LABEL[img.source] || "Your site"}
                  detail={`${img.w}×${img.h}`}
                  badge={img.multipack ? "Multipack" : img.cutout ? "Transparent" : undefined}
                />
              ))}
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="flex aspect-square flex-col items-center justify-center gap-1.5 rounded-md border-[1.5px] border-dashed border-border bg-background p-3 text-center text-[12.5px] text-muted-foreground transition-colors hover:border-faint"
              >
                <span className="text-xl text-faint">+</span>
                <b className="font-semibold text-foreground">Use my own image</b>
                PNG with a plain or transparent background
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                hidden
                onChange={(e) => pickOwn(e.target.files?.[0] || null)}
              />
            </div>
          )}

          {error ? <span className="text-[13px] text-destructive">{error}</span> : null}

          <div className="flex flex-wrap items-center gap-2.5">
            <ButtonLink href={backHref} variant="secondary">
              Back
            </ButtonLink>
            {choice ? (
              <Button
                variant="ghost"
                disabled={saving || !profile}
                onClick={() => save(false)}
                className="text-muted-foreground"
              >
                Skip the image
              </Button>
            ) : null}
            <Button
              variant="primary"
              size="lg"
              className="ml-auto min-w-[240px]"
              disabled={saving || loading || !profile}
              onClick={() => save(true)}
            >
              {saving
                ? "Saving…"
                : unchanged
                  ? "Continue →"
                  : savedBefore
                    ? "Save changes →"
                    : choice
                      ? "Use this image →"
                      : "Continue without an image →"}
            </Button>
          </div>
        </Panel>
      </div>
    </PageShell>
  )
}

function Tile({
  selected,
  onClick,
  src,
  label,
  detail,
  badge,
}: {
  selected: boolean
  onClick: () => void
  src: string
  label: string
  detail: string
  badge?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "group flex flex-col overflow-hidden rounded-md border-[1.5px] border-border bg-card text-left transition-colors",
        selected ? "border-accent ring-2 ring-accent/30" : "hover:border-faint"
      )}
    >
      <div className="relative aspect-square w-full" style={CHECKER}>
        <img src={src} alt={label} className="absolute inset-0 size-full object-contain p-3" />
        {selected ? (
          <span className="absolute right-2 top-2 grid size-5 place-items-center rounded-full bg-accent text-[11px] text-white">
            ✓
          </span>
        ) : null}
      </div>
      <div className="flex items-center gap-1.5 px-2.5 py-2 text-[12px]">
        <div className="min-w-0 flex-1">
          <b className="block truncate font-medium">{label}</b>
          <span className="block truncate font-mono text-[11px] text-muted-foreground">{detail}</span>
        </div>
        {badge ? <Badge className="shrink-0 text-[10.5px]">{badge}</Badge> : null}
      </div>
    </button>
  )
}

export default function ProductImagePage() {
  return (
    <>
      <TopBar step="image" />
      <Suspense fallback={null}>
        <ProductImage />
      </Suspense>
    </>
  )
}
