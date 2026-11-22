"use client"

import { useRouter } from "next/navigation"
import { useEffect, useRef, useState } from "react"

import { TextEffect } from "@/components/motion-primitives/text-effect"
import { TopBar } from "@/components/top-bar"
import { TextureButton } from "@/components/ui/texture-button"
import { TextureCard, TextureCardContent } from "@/components/ui/texture-card"
import { Field, Input, Label, Textarea } from "@/components/ui/input"
import { PageShell } from "@/components/ui/page-shell"
import { research, type ResearchInput } from "@/lib/research"
import { setPendingAsset } from "@/lib/pending-asset"

export default function OnboardPage() {
  const router = useRouter()
  const [form, setForm] = useState<ResearchInput>(research.defaults)
  const [asset, setAsset] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!asset) return setPreview(null)
    const url = URL.createObjectURL(asset)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [asset])

  function submit(e: React.FormEvent) {
    e.preventDefault()
    setPendingAsset(asset)
    const q = new URLSearchParams(form as Record<string, string>)
    router.push(`/research?${q}`)
  }

  const set = (k: keyof ResearchInput) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <>
      <TopBar step="onboard" />
      <PageShell>
        <div className="mx-auto mt-[4vh] grid max-w-[1040px] items-center gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,460px)] lg:gap-14">
          <div>
            <div className="eyebrow">Brand onboarding</div>
            <TextEffect
              as="h1"
              preset="fade-in-blur"
              per="word"
              className="mt-2 font-display text-[clamp(26px,3.2vw,36px)] font-bold leading-[1.1] tracking-tight"
            >
              What are you placing, and who makes it?
            </TextEffect>
            <p className="mt-2.5 max-w-[46ch] text-muted-foreground">
              Give us a name and a product. Our research agent reads your site and public pages to build a
              brand profile, so you don’t have to fill out a long form.
            </p>
            <ul className="mt-7 flex list-none flex-col gap-3.5 p-0">
              {[
                "We look up your website, product pages and socials",
                "We pull out tone, audience, colors and product facts",
                "You pick a creator video and approve each placement",
              ].map((text, i) => (
                <li key={text} className="flex items-center gap-3 text-sm">
                  <span className="grid size-[26px] shrink-0 place-items-center rounded-sm border-[1.5px] border-dashed border-accent font-mono text-xs text-accent-foreground">
                    {i + 1}
                  </span>
                  <span>{text}</span>
                </li>
              ))}
            </ul>
          </div>
          <TextureCard className="shadow-soft">
            <TextureCardContent className="p-0 text-foreground">
            <form className="flex flex-col gap-3.5 p-[22px]" onSubmit={submit}>
              <Field>
                <Label htmlFor="o-biz">Business name</Label>
                <Input id="o-biz" value={form.business} onChange={set("business")} required />
              </Field>
              <Field>
                <Label htmlFor="o-site">
                  Website <span className="text-muted-foreground">(optional, speeds up research)</span>
                </Label>
                <Input id="o-site" value={form.website} onChange={set("website")} />
              </Field>
              <Field>
                <Label htmlFor="o-prod">Product to advertise</Label>
                <Input id="o-prod" value={form.product} onChange={set("product")} required />
              </Field>
              <Field>
                <span className="text-[12.5px] font-medium">
                  Have your own product image? <span className="text-muted-foreground">(optional)</span>
                </span>
                <div
                  className="flex cursor-pointer items-center gap-3.5 rounded-md border-[1.5px] border-dashed border-border bg-background p-2.5"
                  onClick={() => fileRef.current?.click()}
                >
                  {preview ? (
                    <img src={preview} alt="" className="h-[60px] w-11 shrink-0 object-contain" />
                  ) : (
                    <span className="grid h-[60px] w-11 shrink-0 place-items-center rounded-xs bg-muted text-xl text-faint">
                      +
                    </span>
                  )}
                  <div className="min-w-0">
                    <b className="text-[13px] font-semibold">{asset ? asset.name : "Upload a PNG"}</b>
                    <div className="mt-0.5 text-[12.5px] text-muted-foreground">
                      {asset
                        ? `${(asset.size / 1e6).toFixed(1)} MB`
                        : "Otherwise our research agent finds one on your site"}
                    </div>
                  </div>
                  <input
                    ref={fileRef}
                    id="o-img"
                    type="file"
                    accept="image/*"
                    hidden
                    onChange={(e) => setAsset(e.target.files?.[0] || null)}
                  />
                </div>
              </Field>
              <Field>
                <Label htmlFor="o-note">
                  Anything we should know? <span className="text-muted-foreground">(optional)</span>
                </Label>
                <Textarea id="o-note" rows={2} value={form.note} onChange={set("note")} />
              </Field>
              <TextureButton type="submit" variant="primary" size="lg" className="mt-1">
                Research my brand →
              </TextureButton>
            </form>
            </TextureCardContent>
          </TextureCard>
        </div>
      </PageShell>
    </>
  )
}
