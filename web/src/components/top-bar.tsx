"use client"

import Link from "next/link"
import { useEffect, useState } from "react"

import { session } from "@/lib/catalog-client"

const STEPS = [
  ["onboard", "Your product"],
  ["research", "Brand research"],
  ["videos", "Choose a video"],
  ["review", "Review placements"],
] as const

export type Step = (typeof STEPS)[number][0]

export function TopBar({ step, nav }: { step?: Step; nav?: "videos" | "campaigns" | "watch" }) {
  const [biz, setBiz] = useState("")
  useEffect(() => setBiz(session.get()?.profile.business || ""), [])
  const idx = STEPS.findIndex((s) => s[0] === step)
  const initials = biz
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("")

  return (
    <header className="top">
      <div className="top-in">
        <Link href="/" className="logo" aria-label="Inset home">
          <span className="logo-mark" />
          Inset
        </Link>
        <nav className="nav" aria-label="Primary">
          <Link href="/videos" aria-current={nav === "videos" ? "page" : undefined}>
            Videos
          </Link>
          <Link href="/campaigns" aria-current={nav === "campaigns" ? "page" : undefined}>
            Campaigns
          </Link>
          <Link href="/watch" aria-current={nav === "watch" ? "page" : undefined}>
            Watch
          </Link>
        </nav>
        <div className="top-sp" />
        {biz ? (
          <div className="acct">
            <span className="acct-av">{initials || "·"}</span>
            <span>{biz}</span>
          </div>
        ) : null}
      </div>
      {step ? (
        <div className="steps">
          {STEPS.map(([key, label], i) => (
            <span key={key} style={{ display: "contents" }}>
              {i ? <span className="step-line" /> : null}
              <span className={`step ${i < idx ? "done" : i === idx ? "on" : ""}`}>
                <i>{i < idx ? "✓" : i + 1}</i>
                {label}
              </span>
            </span>
          ))}
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
  const node = msg ? <div className="toast">{msg}</div> : null
  return [node, setMsg] as const
}
