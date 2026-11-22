"use client"

import Link from "next/link"
import { useEffect, useState } from "react"

import { Toast } from "@/components/ui/empty-state"
import { cn } from "@/lib/utils"
import { session } from "@/lib/catalog-client"
import { FLOW_EVENT, getFlow, imageHref, researchDone, researchHref, type Flow } from "@/lib/flow"

const STEPS = [
  ["onboard", "Your product"],
  ["research", "Brand research"],
  ["image", "Product image"],
  ["videos", "Choose a video"],
  ["review", "Review placements"],
] as const

export type Step = (typeof STEPS)[number][0]

/** Where each finished step reopens. Research and image reopen the saved run (no new research). */
function stepHrefs(flow: Flow | null): Partial<Record<Step, string>> {
  const hrefs: Partial<Record<Step, string>> = { onboard: "/" }
  if (flow && researchDone(flow)) {
    hrefs.research = researchHref(flow)
    hrefs.image = imageHref(flow)
  }
  if (flow?.brandId) hrefs.videos = "/videos"
  return hrefs
}

export function TopBar({ step, nav }: { step?: Step; nav?: "videos" | "campaigns" | "watch" }) {
  const [biz, setBiz] = useState("")
  const [flow, setFlowState] = useState<Flow | null>(null)
  useEffect(() => {
    setBiz(session.get()?.profile.business || "")
    const sync = () => setFlowState(getFlow())
    sync()
    // A new research run resets the flow; drop the old run's step links.
    window.addEventListener(FLOW_EVENT, sync)
    return () => window.removeEventListener(FLOW_EVENT, sync)
  }, [])
  const idx = STEPS.findIndex((s) => s[0] === step)
  const hrefs = stepHrefs(flow)
  // Steps finished in this run: research done → 2, brand saved → 3.
  const finished = flow?.brandId ? 3 : researchDone(flow) ? 2 : 0
  const initials = biz
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("")

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
        {biz ? (
          <div className="flex items-center gap-2 rounded-full border border-border bg-card py-1 pl-1 pr-2.5 text-[13px] font-medium">
            <span
              className="grid size-6 place-items-center rounded-full text-[11px] font-semibold text-white"
              style={{ background: "var(--account)" }}
            >
              {initials || "·"}
            </span>
            <span className="hidden sm:inline">{biz}</span>
          </div>
        ) : null}
      </div>
      {step ? (
        <div className="mx-auto flex max-w-shell items-center gap-1.5 overflow-x-auto px-4 pb-3 text-[12.5px] sm:px-6">
          {STEPS.map(([key, label], i) => {
            const done = i !== idx && (i < idx || i < finished)
            const href = i !== idx ? hrefs[key] : undefined
            const body = (
              <>
                <i
                  className={cn(
                    "grid size-[18px] place-items-center rounded-full border border-current font-mono text-[10px] not-italic",
                    done && "border-accent bg-accent text-white",
                    i === idx && "border-foreground bg-foreground text-background"
                  )}
                >
                  {done ? "✓" : i + 1}
                </i>
                {label}
              </>
            )
            const cls = cn(
              "flex shrink-0 items-center gap-1.5 whitespace-nowrap text-faint",
              done && "text-muted-foreground",
              i === idx && "font-semibold text-foreground"
            )
            return (
              <span key={key} className="contents">
                {i ? <span className="h-px w-7 shrink-0 bg-border" /> : null}
                {href ? (
                  <Link
                    href={href}
                    className={cn(cls, "rounded-sm transition-colors duration-150 hover:text-foreground")}
                    title={`Back to ${label.toLowerCase()}`}
                  >
                    {body}
                  </Link>
                ) : (
                  <span className={cls} aria-current={i === idx ? "step" : undefined}>
                    {body}
                  </span>
                )}
              </span>
            )
          })}
        </div>
      ) : null}
    </header>
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
