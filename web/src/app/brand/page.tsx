"use client"

import Link from "next/link"
import { useEffect, useMemo, useState } from "react"

import { SiteNav } from "@/components/site-nav"

type Brand = {
  id: string
  name: string
  brief?: string
  rules?: { prefer?: string[]; avoid?: string[] }
}

type Title = { id: string; name: string; slug: string; kind: string }
type Episode = {
  id: string
  name: string
  season: number
  episode_number: number
  suggest_status?: string
}
type Moment = {
  id: string
  start_s: number
  end_s: number
  whats_there?: string
  score?: number
  reason?: string
  frame_key?: string | null
  brand_fit?: { total?: number; vetoed?: boolean; reason?: string } | null
  placement?: { id: string; status: string } | null
}

function mediaUrl(key?: string | null) {
  if (!key) return null
  return `/api/media/${key.split("/").map(encodeURIComponent).join("/")}`
}

export default function BrandStudioPage() {
  const [brands, setBrands] = useState<Brand[]>([])
  const [brandId, setBrandId] = useState("")
  const [titles, setTitles] = useState<Title[]>([])
  const [titleId, setTitleId] = useState("")
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [selectedEps, setSelectedEps] = useState<string[]>([])
  const [moments, setMoments] = useState<Moment[]>([])
  const [testMode, setTestMode] = useState(true)
  const [status, setStatus] = useState("")
  const [busy, setBusy] = useState(false)
  const [brandForm, setBrandForm] = useState({
    name: "",
    brief: "Prefer cups and bottles on a table. Avoid beds.",
    asset: null as File | null,
  })

  const selectedTitle = useMemo(
    () => titles.find((t) => t.id === titleId),
    [titles, titleId]
  )

  async function loadBrands() {
    const res = await fetch("/api/catalog/brands")
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || "brands failed")
    setBrands(data.brands || [])
    if (!brandId && data.brands?.[0]?.id) setBrandId(data.brands[0].id)
  }

  async function loadTitles() {
    const res = await fetch("/api/catalog/titles")
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || "titles failed")
    setTitles(data.titles || [])
    if (!titleId && data.titles?.[0]?.id) setTitleId(data.titles[0].id)
  }

  async function loadEpisodes(tid: string) {
    if (!tid) return
    const res = await fetch(`/api/catalog/titles/${tid}/episodes`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || "episodes failed")
    setEpisodes(data.episodes || [])
    setSelectedEps((data.episodes || []).map((e: Episode) => e.id))
  }

  async function loadMoments(epIds: string[], bid: string) {
    if (!bid || epIds.length === 0) {
      setMoments([])
      return
    }
    const all: Moment[] = []
    for (const epId of epIds) {
      const res = await fetch(
        `/api/catalog/moments?episodeId=${encodeURIComponent(epId)}&brandId=${encodeURIComponent(bid)}`
      )
      const data = await res.json()
      if (res.ok) all.push(...(data.moments || []))
    }
    all.sort((a, b) => (b.brand_fit?.total || b.score || 0) - (a.brand_fit?.total || a.score || 0))
    setMoments(all)

    const placesRes = await fetch(
      `/api/catalog/placements?brandId=${encodeURIComponent(bid)}`
    )
    const placesData = await placesRes.json()
    const byMoment = new Map<string, { id: string; status: string }>()
    for (const p of placesData.placements || []) {
      byMoment.set(p.moment_id, { id: p.id, status: p.status })
    }
    setMoments((ms) =>
      ms.map((m) => ({ ...m, placement: byMoment.get(m.id) || null }))
    )
  }

  useEffect(() => {
    Promise.all([loadBrands(), loadTitles()]).catch((e) =>
      setStatus(String(e.message || e))
    )
  }, [])

  useEffect(() => {
    loadEpisodes(titleId).catch((e) => setStatus(String(e.message || e)))
  }, [titleId])

  useEffect(() => {
    loadMoments(selectedEps, brandId).catch((e) =>
      setStatus(String(e.message || e))
    )
    const t = window.setInterval(() => {
      loadMoments(selectedEps, brandId).catch(() => {})
    }, 4000)
    return () => window.clearInterval(t)
  }, [selectedEps.join(","), brandId])

  async function createBrand(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      const form = new FormData()
      form.set("name", brandForm.name)
      form.set("brief", brandForm.brief)
      if (brandForm.asset) form.set("asset", brandForm.asset)
      const res = await fetch("/api/catalog/brands", { method: "POST", body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || "create brand failed")
      await loadBrands()
      setBrandId(data.id)
      setStatus(`Brand ${data.name} ready`)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function decide(momentId: string, statusValue: "approved" | "rejected") {
    if (!brandId) return
    setBusy(true)
    setStatus(`${statusValue}…`)
    try {
      const res = await fetch("/api/catalog/placements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          brandId,
          momentId,
          status: statusValue,
          testMode,
          confirm: true,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || "placement failed")
      await loadMoments(selectedEps, brandId)
      setStatus(
        statusValue === "approved"
          ? `Queued swap (${testMode ? "test/mock" : "paid"}) · ${data.id}`
          : `Rejected ${data.id}`
      )
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  function toggleAllEpisodes() {
    if (selectedEps.length === episodes.length) setSelectedEps([])
    else setSelectedEps(episodes.map((e) => e.id))
  }

  return (
    <div className="min-h-screen bg-[#101014] text-zinc-100">
      <SiteNav />
      <main className="mx-auto max-w-6xl px-5 pb-16">
        <h1 className="font-[family-name:var(--font-display)] text-3xl tracking-tight text-white">
          Brand studio
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-zinc-400">
          Pick a title and episodes, review brand-fit moments, approve to queue a
          placement. Test mode stays on by default (mock swap).
        </p>

        <div className="mt-8 grid gap-6 lg:grid-cols-[280px_1fr]">
          <aside className="space-y-4">
            <form
              onSubmit={createBrand}
              className="space-y-2 rounded-xl bg-white/5 p-4 ring-1 ring-white/10"
            >
              <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                New brand
              </h2>
              <input
                className="w-full rounded border border-white/10 bg-black/40 px-3 py-2 text-sm"
                placeholder="Brand name"
                value={brandForm.name}
                onChange={(e) =>
                  setBrandForm((f) => ({ ...f, name: e.target.value }))
                }
                required
              />
              <textarea
                className="w-full rounded border border-white/10 bg-black/40 px-3 py-2 text-sm"
                rows={3}
                value={brandForm.brief}
                onChange={(e) =>
                  setBrandForm((f) => ({ ...f, brief: e.target.value }))
                }
              />
              <input
                type="file"
                accept="image/*"
                onChange={(e) =>
                  setBrandForm((f) => ({
                    ...f,
                    asset: e.target.files?.[0] || null,
                  }))
                }
              />
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-md bg-emerald-700 px-3 py-2 text-sm disabled:opacity-50"
              >
                Create brand
              </button>
            </form>

            <label className="block text-xs uppercase tracking-wide text-zinc-500">
              Active brand
              <select
                className="mt-1 w-full rounded border border-white/10 bg-black/40 px-3 py-2 text-sm"
                value={brandId}
                onChange={(e) => setBrandId(e.target.value)}
              >
                <option value="">Select…</option>
                {brands.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-xs uppercase tracking-wide text-zinc-500">
              Title
              <select
                className="mt-1 w-full rounded border border-white/10 bg-black/40 px-3 py-2 text-sm"
                value={titleId}
                onChange={(e) => setTitleId(e.target.value)}
              >
                <option value="">Select…</option>
                {titles.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>

            <div>
              <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wide text-zinc-500">
                <span>Episodes</span>
                <button
                  type="button"
                  className="text-zinc-300"
                  onClick={toggleAllEpisodes}
                >
                  {selectedEps.length === episodes.length ? "None" : "All"}
                </button>
              </div>
              <div className="max-h-56 space-y-1 overflow-auto rounded border border-white/10 p-2">
                {episodes.map((ep) => (
                  <label
                    key={ep.id}
                    className="flex items-center gap-2 text-sm text-zinc-300"
                  >
                    <input
                      type="checkbox"
                      checked={selectedEps.includes(ep.id)}
                      onChange={(e) => {
                        setSelectedEps((ids) =>
                          e.target.checked
                            ? [...ids, ep.id]
                            : ids.filter((id) => id !== ep.id)
                        )
                      }}
                    />
                    <span>
                      S{ep.season}E{ep.episode_number} · {ep.name}
                    </span>
                    <span className="ml-auto text-xs text-zinc-500">
                      {ep.suggest_status}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={testMode}
                onChange={(e) => setTestMode(e.target.checked)}
              />
              Test mode (mock swap)
            </label>
          </aside>

          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
                Candidate moments
              </h2>
              {selectedTitle && brandId ? (
                <Link
                  href={`/watch/${selectedTitle.slug}/1-1?brandId=${brandId}`}
                  className="text-sm text-emerald-400 hover:text-emerald-300"
                >
                  Open watch →
                </Link>
              ) : null}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              {moments.map((m) => {
                const frame = mediaUrl(m.frame_key)
                const fit = m.brand_fit?.total
                const placeStatus = m.placement?.status
                return (
                  <article
                    key={m.id}
                    className="overflow-hidden rounded-xl bg-white/5 ring-1 ring-white/10"
                  >
                    <div className="aspect-video bg-black">
                      {frame ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={frame}
                          alt=""
                          className="h-full w-full object-cover"
                        />
                      ) : null}
                    </div>
                    <div className="space-y-2 p-3 text-sm">
                      <div className="flex justify-between gap-2">
                        <span className="text-zinc-200">
                          {m.whats_there || "moment"}
                        </span>
                        <span className="text-xs text-zinc-500">
                          {m.start_s.toFixed(1)}–{m.end_s.toFixed(1)}s
                        </span>
                      </div>
                      <div className="text-xs text-zinc-500">
                        score {m.score?.toFixed?.(2) ?? m.score}
                        {fit != null ? ` · brand_fit ${fit.toFixed(2)}` : ""}
                        {m.brand_fit?.vetoed ? " · vetoed" : ""}
                      </div>
                      <div className="text-xs text-zinc-400">
                        Status: {placeStatus || "none"}
                      </div>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={busy || placeStatus === "ready" || placeStatus === "queued" || placeStatus === "rendering"}
                          onClick={() => decide(m.id, "approved")}
                          className="rounded bg-emerald-700 px-2 py-1 text-xs disabled:opacity-40"
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => decide(m.id, "rejected")}
                          className="rounded bg-zinc-700 px-2 py-1 text-xs disabled:opacity-40"
                        >
                          Reject
                        </button>
                      </div>
                    </div>
                  </article>
                )
              })}
              {moments.length === 0 ? (
                <p className="text-sm text-zinc-500">
                  No moments yet. Seed/upload episodes and wait for suggest=ready.
                </p>
              ) : null}
            </div>
          </section>
        </div>

        {status ? (
          <p className="mt-6 whitespace-pre-wrap text-sm text-zinc-400">{status}</p>
        ) : null}
      </main>
    </div>
  )
}
