"use client"

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { Suspense, useEffect, useMemo, useRef, useState } from "react"

import { TopBar } from "@/components/top-bar"
import { createBrand, session } from "@/lib/catalog-client"
import { takePendingAsset } from "@/lib/pending-asset"
import { briefFor, research, type BrandProfile, type ResearchInput } from "@/lib/research"

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
    <main className="wrap">
      <div className="rs">
        <div>
          <div className="eyebrow">{done ? "Profile ready" : "Researching"}</div>
          <h1 style={{ marginTop: 8 }}>
            {done ? `Here’s what we learned about ${input.business}` : `Reading up on ${input.business}`}
          </h1>
          <p className="muted" style={{ marginTop: 8 }}>
            {done
              ? "Check it over. We use this to keep every placement on-brand."
              : "A TinyFish web agent is searching and fetching your public pages. This usually takes under a minute."}
          </p>
          <div className="panel rs-run">
            <div className="rs-bar"><i style={{ width: `${pct}%` }} /></div>
            <ul className="rs-steps">
              {steps.map((s, i) => {
                const cls = i < stepIdx ? "ok" : i === stepIdx && !error ? "wip" : ""
                return (
                  <li key={s.label} className={cls}>
                    <i>{i < stepIdx ? "✓" : ""}</i>
                    <span>{s.label}<small>{s.detail}</small></span>
                  </li>
                )
              })}
            </ul>
            {error ? (
              <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <span className="error">Couldn’t save the brand profile: {error}</span>
                <button className="btn btn-sm" onClick={() => setAttempt((a) => a + 1)}>Try again</button>
              </div>
            ) : null}
          </div>
          <div className="panel rs-src">
            <div className="rs-src-h">
              <span className="eyebrow">Sources fetched</span>
              <span className="mono muted">{sources}</span>
            </div>
            <ul>
              {allSources.slice(0, sources).map((s) => (
                <li key={s.url}>
                  <span>{s.kind}</span>
                  <b>{s.url}</b>
                  <em>{(s.ms / 1000).toFixed(1)}s</em>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <div>
          <div className="panel prof">
            <div className="prof-h">
              <div className="prof-logo">{input.business[0] || "·"}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <b style={{ fontWeight: 600 }}>{input.business}</b>
                <div className="card-s">
                  {profile ? [profile.website, profile.location].filter(Boolean).join(" · ") : input.website}
                </div>
              </div>
              {done ? <span className="tag"><span className="dot" />Saved</span> : <span className="tag tag-amber">Building…</span>}
            </div>
            {profile ? (
              <>
                <dl>
                  <dt>Product</dt>
                  <dd><b style={{ fontWeight: 600 }}>{profile.product}</b><div className="card-s">{profile.productFacts}</div></dd>
                  <dt>Category</dt><dd>{profile.category}</dd>
                  <dt>Tone</dt><dd>{profile.tone}</dd>
                  <dt>Audience</dt><dd>{profile.audience}</dd>
                  <dt>Colors</dt>
                  <dd className="sw-row">{profile.colors.map((c) => <span key={c} className="sw-c" style={{ background: c }} />)}</dd>
                  <dt>Good scenes</dt>
                  <dd className="kw">{profile.goodScenes.map((s) => <span key={s}>{s}</span>)}</dd>
                  <dt>Avoid</dt>
                  <dd className="kw no">{profile.avoidScenes.map((s) => <span key={s}>{s}</span>)}</dd>
                </dl>
                <details>
                  <summary className="muted" style={{ fontSize: 12.5, cursor: "pointer" }}>Brief sent to backend</summary>
                  <div className="json">{briefFor(profile, input.note)}</div>
                </details>
                <div style={{ display: "flex", gap: 10 }}>
                  <Link href="/" className="btn">Edit</Link>
                  <Link href="/videos" className="btn btn-primary btn-lg" style={{ flex: 1 }}>Browse creator videos →</Link>
                </div>
              </>
            ) : (
              <>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {[90, 70, 80, 55, 75, 60].map((w, i) => <div key={i} className="sk" style={{ width: `${w}%` }} />)}
                </div>
                <p className="note">Your profile fills in as sources come back.</p>
              </>
            )}
          </div>
        </div>
      </div>
    </main>
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
