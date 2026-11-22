"use client"

import { useCallback, useEffect, useState } from "react"

import { HaloButton } from "@/components/ui/halo-button"
import { HaloDropzone } from "@/components/ui/halo-dropzone"
import { HaloField, HaloFieldDescription, HaloFieldLabel } from "@/components/ui/halo-field"
import { HaloInput } from "@/components/ui/halo-input"
import type { Brand, BrandRules } from "@/lib/types"

const EXAMPLE_BRIEFS = [
  "Prefer cups and bottles resting on a dining table or kitchen counter. Avoid beds, laptops, and close-up faces.",
  "Place the product held in hand in the foreground. Prefer cups. Avoid dining tables and background shelves.",
  "Prefer empty beds, suitcases, and soft surfaces in the background. Avoid wine glasses, bottles, and dining tables.",
]

export function BrandsPanel({
  selectedId,
  onSelect,
  onBrandsChange,
}: {
  selectedId: string | null
  onSelect: (brand: Brand | null) => void
  onBrandsChange?: (brands: Brand[]) => void
}) {
  const [brands, setBrands] = useState<Brand[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const [name, setName] = useState("")
  const [brief, setBrief] = useState(EXAMPLE_BRIEFS[0])
  const [rules, setRules] = useState<BrandRules | null>(null)
  const [assets, setAssets] = useState<File[]>([])
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!assets[0]) {
      setPreviewUrl(null)
      return
    }
    const url = URL.createObjectURL(assets[0])
    setPreviewUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [assets])

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch("/api/brands")
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || res.statusText)
      const list = (data.brands || []) as Brand[]
      setBrands(list)
      onBrandsChange?.(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [onBrandsChange])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function parseBrief(text: string) {
    const res = await fetch("/api/brands/parse-brief", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief: text }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || res.statusText)
    setRules(data as BrandRules)
  }

  useEffect(() => {
    if (!creating) return
    const t = setTimeout(() => {
      void parseBrief(brief).catch(() => undefined)
    }, 350)
    return () => clearTimeout(t)
  }, [brief, creating])

  async function saveBrand() {
    setSaving(true)
    setError(null)
    try {
      if (!name.trim()) throw new Error("Give the brand a name")
      if (!assets.length) throw new Error("Add at least one product image")
      const form = new FormData()
      form.set("name", name.trim())
      form.set("brief", brief)
      form.set("prefer", (rules?.prefer || []).join("|"))
      form.set("avoid", (rules?.avoid || []).join("|"))
      form.set("placement", rules?.placement || "resting")
      form.set("min_area", String(rules?.min_area ?? 0.02))
      for (const f of assets) form.append("assets", f)
      const res = await fetch("/api/brands", { method: "POST", body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || res.statusText)
      setCreating(false)
      setName("")
      setAssets([])
      await refresh()
      onSelect(data as Brand)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  function removeChip(kind: "prefer" | "avoid", value: string) {
    if (!rules) return
    setRules({
      ...rules,
      [kind]: rules[kind].filter((x) => x !== value),
    })
  }

  return (
    <section className="space-y-5 rounded-[28px] border border-black/5 bg-white/75 p-6 backdrop-blur">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
            Brands
          </h2>
          <p className="mt-1 max-w-xl text-sm text-[var(--ink-soft)]">
            Onboard a product and brief. The brief becomes prefer/avoid rules that
            change which moments we pick — not just the swap prompt.
          </p>
        </div>
        <HaloButton
          type="button"
          translucent={false}
          onClick={() => {
            setCreating((v) => !v)
            setError(null)
          }}
        >
          {creating ? "Cancel" : "New brand"}
        </HaloButton>
      </div>

      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          {error}
        </div>
      )}

      {creating && (
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="space-y-4">
            <HaloField>
              <HaloFieldLabel>Brand name</HaloFieldLabel>
              <HaloInput
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Acme Cola"
              />
            </HaloField>
            <HaloDropzone
              accept="image/*,.png,.jpg,.jpeg,.webp"
              multiple
              onFiles={(files) => setAssets(files)}
              emptyLabel="Drop product images"
              formatHint="PNG with alpha works best"
              successLabel={assets.length ? `${assets.length} image(s)` : "Ready"}
              className="min-h-[160px]"
            />
            {previewUrl && (
              <img
                src={previewUrl}
                alt="Product preview"
                className="h-24 w-24 rounded-xl object-contain bg-neutral-100"
              />
            )}
          </div>
          <div className="space-y-4">
            <HaloField>
              <HaloFieldLabel>Brief</HaloFieldLabel>
              <textarea
                className="min-h-[110px] w-full rounded-2xl border border-black/10 bg-white/80 px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-[var(--place)]"
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
              />
              <HaloFieldDescription>
                Start from an example:
              </HaloFieldDescription>
              <div className="mt-2 flex flex-wrap gap-2">
                {EXAMPLE_BRIEFS.map((ex, i) => (
                  <button
                    key={i}
                    type="button"
                    className="rounded-full border border-black/10 bg-white px-3 py-1 text-left text-xs text-[var(--ink-soft)] hover:border-[var(--place)]"
                    onClick={() => setBrief(ex)}
                  >
                    Example {i + 1}
                  </button>
                ))}
              </div>
            </HaloField>

            {rules && (
              <div className="space-y-3 rounded-2xl border border-black/5 bg-[var(--accent)]/40 p-4">
                <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  How we read the brief · {rules.placement} · min area{" "}
                  {(rules.min_area * 100).toFixed(0)}%
                </p>
                <div>
                  <p className="mb-1 text-xs font-medium text-[var(--ink)]">Prefer</p>
                  <div className="flex flex-wrap gap-2">
                    {rules.prefer.length === 0 && (
                      <span className="text-xs text-muted-foreground">none yet</span>
                    )}
                    {rules.prefer.map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => removeChip("prefer", p)}
                        className="rounded-full bg-[var(--place)]/15 px-3 py-1 text-xs text-[var(--ink)]"
                        title="Remove"
                      >
                        {p} ×
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium text-[var(--ink)]">Avoid</p>
                  <div className="flex flex-wrap gap-2">
                    {rules.avoid.length === 0 && (
                      <span className="text-xs text-muted-foreground">none yet</span>
                    )}
                    {rules.avoid.map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => removeChip("avoid", p)}
                        className="rounded-full bg-red-100 px-3 py-1 text-xs text-red-900"
                        title="Remove"
                      >
                        {p} ×
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            <HaloButton
              type="button"
              isLoading={saving}
              loadingText="Saving…"
              onClick={saveBrand}
              disabled={saving}
            >
              Save brand
            </HaloButton>
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onSelect(null)}
          className={`rounded-full px-4 py-2 text-sm ${
            !selectedId
              ? "bg-[var(--ink)] text-white"
              : "border border-black/10 bg-white text-[var(--ink-soft)]"
          }`}
        >
          No brand
        </button>
        {loading && (
          <span className="self-center font-mono text-xs text-muted-foreground">
            Loading…
          </span>
        )}
        {!loading && brands.length === 0 && !creating && (
          <span className="self-center text-sm text-muted-foreground">
            No brands yet — create one to steer moment picks.
          </span>
        )}
        {brands.map((b) => (
          <button
            key={b.id}
            type="button"
            onClick={() => onSelect(b)}
            className={`rounded-full px-4 py-2 text-sm ${
              selectedId === b.id
                ? "bg-[var(--place)] text-white"
                : "border border-black/10 bg-white text-[var(--ink)]"
            }`}
          >
            {b.name}
          </button>
        ))}
      </div>
    </section>
  )
}
