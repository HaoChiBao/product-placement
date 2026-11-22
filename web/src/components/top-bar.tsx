"use client"

import Link from "next/link"
import { useEffect, useRef, useState } from "react"

import { Toast } from "@/components/ui/empty-state"
import { cn } from "@/lib/utils"
import { session } from "@/lib/catalog-client"
import type { BrandProfile } from "@/lib/research"

const STEPS = [
  ["onboard", "Your product"],
  ["research", "Brand research"],
  ["image", "Product image"],
  ["videos", "Choose a video"],
  ["review", "Review placements"],
] as const

export type Step = (typeof STEPS)[number][0]

/** Steps before the brand is saved; a session from an earlier run would be stale here. */
const PRE_BRAND: Step[] = ["onboard", "research", "image"]

export function TopBar({ step, nav }: { step?: Step; nav?: "videos" | "campaigns" | "watch" }) {
  const [profile, setProfile] = useState<BrandProfile | null>(null)
  useEffect(() => {
    if (step && PRE_BRAND.includes(step)) return
    setProfile(session.get()?.profile || null)
  }, [step])
  const idx = STEPS.findIndex((s) => s[0] === step)

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/88 backdrop-blur-[10px]">
      <div className="mx-auto flex max-w-shell items-center gap-5 px-4 py-3 sm:px-6">
        <Link
          href="/"
          className="flex items-center gap-2 font-display text-[19px] font-bold tracking-tight"
          aria-label="Inset home"
        >
          <span className="logo-mark" />
          Inset
        </Link>
        <nav className="hidden gap-1 md:flex" aria-label="Primary">
          {(
            [
              ["/videos", "videos", "Videos"],
              ["/campaigns", "campaigns", "Campaigns"],
              ["/watch", "watch", "Watch"],
            ] as const
          ).map(([href, key, label]) => (
            <Link
              key={key}
              href={href}
              aria-current={nav === key ? "page" : undefined}
              className={cn(
                "rounded-sm px-2.5 py-1.5 font-medium text-muted-foreground transition-colors duration-150",
                nav === key && "bg-muted text-foreground"
              )}
            >
              {label}
            </Link>
          ))}
        </nav>
        <div className="flex-1" />
        {profile?.business ? <BrandMenu profile={profile} /> : null}
      </div>
      {step ? (
        <div className="mx-auto flex max-w-shell items-center gap-1.5 overflow-x-auto px-4 pb-3 text-[12.5px] sm:px-6">
          {STEPS.map(([key, label], i) => (
            <span key={key} className="contents">
              {i ? <span className="h-px w-7 shrink-0 bg-border" /> : null}
              <span
                className={cn(
                  "flex shrink-0 items-center gap-1.5 whitespace-nowrap text-faint",
                  i < idx && "text-muted-foreground",
                  i === idx && "font-semibold text-foreground"
                )}
              >
                <i
                  className={cn(
                    "grid size-[18px] place-items-center rounded-full border border-current font-mono text-[10px] not-italic",
                    i < idx && "border-accent bg-accent text-white",
                    i === idx && "border-foreground bg-foreground text-background"
                  )}
                >
                  {i < idx ? "✓" : i + 1}
                </i>
                {label}
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </header>
  )
}

function BrandMenu({ profile }: { profile: BrandProfile }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const initials = profile.business
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("")

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false)
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
  }, [open])

  const rows = (
    [
      ["Product", profile.product],
      ["Category", profile.category],
      ["Audience", profile.audience],
      ["Tone", profile.tone],
    ] as const
  ).filter(([, v]) => v?.trim())

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={cn(
          "flex items-center gap-2 rounded-full border border-border bg-card py-1 pl-1 pr-2.5 text-[13px] font-medium transition-colors duration-150 hover:bg-muted",
          open && "bg-muted"
        )}
      >
        <span
          className="grid size-6 place-items-center rounded-full text-[11px] font-semibold text-white"
          style={{ background: "var(--account)" }}
        >
          {initials || "·"}
        </span>
        <span className="hidden sm:inline">{profile.business}</span>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label="Brand summary"
          className="absolute right-0 top-[calc(100%+8px)] z-30 w-[min(360px,calc(100vw-32px))] rounded-lg border border-border bg-card p-4 text-[13px] shadow-soft"
        >
          <div className="eyebrow">What we understood</div>
          <div className="mt-1 font-display text-[17px] font-bold leading-tight tracking-tight">
            {profile.business}
          </div>
          {profile.website ? (
            <div className="mt-0.5 truncate text-[12.5px] text-muted-foreground">{profile.website}</div>
          ) : null}
          {profile.productFacts ? (
            <p className="mt-3 leading-[1.5] text-foreground">{profile.productFacts}</p>
          ) : null}
          {rows.length ? (
            <dl className="mt-3 grid grid-cols-[84px_1fr] gap-x-3 gap-y-1.5 border-t border-border pt-3">
              {rows.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="m-0">{v}</dd>
                </div>
              ))}
              {profile.colors?.length ? (
                <div className="contents">
                  <dt className="text-muted-foreground">Colors</dt>
                  <dd className="m-0 flex items-center gap-1.5">
                    {profile.colors.slice(0, 6).map((c) => (
                      <span
                        key={c}
                        title={c}
                        className="size-3.5 rounded-full border border-black/10"
                        style={{ background: c }}
                      />
                    ))}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}
          {profile.goodScenes?.length || profile.avoidScenes?.length ? (
            <div className="mt-3 flex flex-col gap-1.5 border-t border-border pt-3">
              {profile.goodScenes?.length ? (
                <div>
                  <span className="text-muted-foreground">Good fit: </span>
                  {profile.goodScenes.join(", ")}
                </div>
              ) : null}
              {profile.avoidScenes?.length ? (
                <div>
                  <span className="text-muted-foreground">Avoid: </span>
                  {profile.avoidScenes.join(", ")}
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="mt-4 flex justify-end border-t border-border pt-3">
            <Link
              href="/"
              onClick={() => setOpen(false)}
              className="text-[12.5px] font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              Start over with a new product
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function useToast() {
  const [msg, setMsg] = useState("")
  useEffect(() => {
    if (!msg) return
    const t = window.setTimeout(() => setMsg(""), 2000)
    return () => window.clearTimeout(t)
  }, [msg])
  const node = msg ? <Toast>{msg}</Toast> : null
  return [node, setMsg] as const
}
