"use client"

import { useRouter } from "next/navigation"
import { useEffect, useRef, useState } from "react"

import { TopBar } from "@/components/top-bar"
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
      <main className="wrap">
        <div className="ob">
          <div className="ob-intro">
            <div className="eyebrow">Brand onboarding</div>
            <h1 style={{ marginTop: 8 }}>What are you placing, and who makes it?</h1>
            <p className="muted" style={{ marginTop: 10 }}>
              Give us a name and a product. Our research agent reads your site and public pages to build a
              brand profile, so you don’t have to fill out a long form.
            </p>
            <ul className="ob-list">
              <li><span className="ob-k">1</span><span>We look up your website, product pages and socials</span></li>
              <li><span className="ob-k">2</span><span>We pull out tone, audience, colors and product facts</span></li>
              <li><span className="ob-k">3</span><span>You pick a creator video and approve each placement</span></li>
            </ul>
          </div>
          <form className="panel sec ob-form" onSubmit={submit}>
            <div className="f">
              <label htmlFor="o-biz">Business name</label>
              <input className="inp" id="o-biz" value={form.business} onChange={set("business")} required />
            </div>
            <div className="f">
              <label htmlFor="o-site">Website <span className="muted">(optional, speeds up research)</span></label>
              <input className="inp" id="o-site" value={form.website} onChange={set("website")} />
            </div>
            <div className="f">
              <label htmlFor="o-prod">Product to advertise</label>
              <input className="inp" id="o-prod" value={form.product} onChange={set("product")} required />
            </div>
            <div className="f">
              <span className="lbl">
                Product image <span className="muted">(optional, we can pull one from your site)</span>
              </span>
              <div className="ob-drop" onClick={() => fileRef.current?.click()}>
                {preview ? <img src={preview} alt="" /> : <span className="ob-drop-ph">+</span>}
                <div style={{ minWidth: 0 }}>
                  <b style={{ fontWeight: 600, fontSize: 13 }}>{asset ? asset.name : "Upload a PNG"}</b>
                  <div className="card-s">
                    {asset ? `${(asset.size / 1e6).toFixed(1)} MB` : "Plain or transparent background works best"}
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
            </div>
            <div className="f">
              <label htmlFor="o-note">Anything we should know? <span className="muted">(optional)</span></label>
              <textarea className="inp" id="o-note" rows={2} value={form.note} onChange={set("note")} />
            </div>
            <button className="btn btn-primary btn-lg" style={{ width: "100%" }}>
              Research my brand →
            </button>
          </form>
        </div>
      </main>
    </>
  )
}
