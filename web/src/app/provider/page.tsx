"use client"

import { useEffect, useState } from "react"

import { SiteNav } from "@/components/site-nav"

type Title = {
  id: string
  name: string
  slug: string
  kind: string
  synopsis?: string
  attribution?: string
}

type Episode = {
  id: string
  name: string
  season: number
  episode_number: number
  suggest_status?: string
  duration_s?: number
  poster_key?: string | null
}

export default function ProviderPage() {
  const [titles, setTitles] = useState<Title[]>([])
  const [selected, setSelected] = useState<string>("")
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [status, setStatus] = useState("")
  const [busy, setBusy] = useState(false)

  const [titleForm, setTitleForm] = useState({
    name: "",
    kind: "series",
    synopsis: "",
    attribution: "",
  })
  const [epForm, setEpForm] = useState({
    name: "",
    season: 1,
    episode: 1,
    file: null as File | null,
  })

  async function refreshTitles() {
    const res = await fetch("/api/catalog/titles")
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || "list titles failed")
    setTitles(data.titles || [])
    if (!selected && data.titles?.[0]?.id) setSelected(data.titles[0].id)
  }

  async function refreshEpisodes(titleId: string) {
    if (!titleId) {
      setEpisodes([])
      return
    }
    const res = await fetch(`/api/catalog/titles/${titleId}/episodes`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || "list episodes failed")
    setEpisodes(data.episodes || [])
  }

  useEffect(() => {
    refreshTitles().catch((e) => setStatus(String(e.message || e)))
  }, [])

  useEffect(() => {
    refreshEpisodes(selected).catch((e) => setStatus(String(e.message || e)))
    const t = window.setInterval(() => {
      if (selected) refreshEpisodes(selected).catch(() => {})
    }, 4000)
    return () => window.clearInterval(t)
  }, [selected])

  async function createTitle(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setStatus("")
    try {
      const res = await fetch("/api/catalog/titles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(titleForm),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || "create failed")
      setTitleForm({ name: "", kind: "series", synopsis: "", attribution: "" })
      await refreshTitles()
      setSelected(data.id)
      setStatus(`Created title ${data.name}`)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function uploadEpisode(e: React.FormEvent) {
    e.preventDefault()
    if (!selected || !epForm.file) {
      setStatus("Pick a title and video file")
      return
    }
    setBusy(true)
    setStatus("Uploading…")
    try {
      const form = new FormData()
      form.set("name", epForm.name || epForm.file.name)
      form.set("season", String(epForm.season))
      form.set("episode", String(epForm.episode))
      form.set("video", epForm.file)
      const res = await fetch(`/api/catalog/titles/${selected}/episodes`, {
        method: "POST",
        body: form,
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || "upload failed")
      setEpForm({ name: "", season: 1, episode: epForm.episode + 1, file: null })
      await refreshEpisodes(selected)
      setStatus(`Episode uploaded; suggest queued (${data.id})`)
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function seed() {
    setBusy(true)
    setStatus("Seeding Blender open movies…")
    try {
      const res = await fetch("/api/catalog/seed", { method: "POST" })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || "seed failed")
      await refreshTitles()
      setStatus(
        data.skipped
          ? "Catalog already seeded"
          : `Seeded ${(data.titles || []).length} titles`
      )
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#f4f1ea] text-zinc-900">
      <SiteNav tone="light" />
      <main className="mx-auto max-w-5xl px-5 pb-16">
        <h1 className="font-[family-name:var(--font-display)] text-3xl tracking-tight">
          Provider
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-zinc-600">
          Upload shows and movies. Each episode runs the moment finder in the
          background so brands can pick placements later.
        </p>

        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={seed}
            disabled={busy}
            className="rounded-md bg-zinc-900 px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            Seed Blender catalog
          </button>
        </div>

        <div className="mt-8 grid gap-8 md:grid-cols-2">
          <form
            onSubmit={createTitle}
            className="space-y-3 rounded-xl bg-white/70 p-4 ring-1 ring-zinc-200"
          >
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              New title
            </h2>
            <input
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
              placeholder="Title name"
              value={titleForm.name}
              onChange={(e) =>
                setTitleForm((f) => ({ ...f, name: e.target.value }))
              }
              required
            />
            <select
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
              value={titleForm.kind}
              onChange={(e) =>
                setTitleForm((f) => ({ ...f, kind: e.target.value }))
              }
            >
              <option value="series">Series</option>
              <option value="movie">Movie</option>
            </select>
            <textarea
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
              placeholder="Synopsis"
              value={titleForm.synopsis}
              onChange={(e) =>
                setTitleForm((f) => ({ ...f, synopsis: e.target.value }))
              }
            />
            <input
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
              placeholder="Attribution"
              value={titleForm.attribution}
              onChange={(e) =>
                setTitleForm((f) => ({ ...f, attribution: e.target.value }))
              }
            />
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-emerald-800 px-3 py-2 text-sm text-white disabled:opacity-50"
            >
              Create title
            </button>
          </form>

          <form
            onSubmit={uploadEpisode}
            className="space-y-3 rounded-xl bg-white/70 p-4 ring-1 ring-zinc-200"
          >
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Upload episode
            </h2>
            <select
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">Select title…</option>
              {titles.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <input
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
              placeholder="Episode name"
              value={epForm.name}
              onChange={(e) =>
                setEpForm((f) => ({ ...f, name: e.target.value }))
              }
            />
            <div className="flex gap-2">
              <input
                type="number"
                min={1}
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
                value={epForm.season}
                onChange={(e) =>
                  setEpForm((f) => ({ ...f, season: Number(e.target.value) }))
                }
              />
              <input
                type="number"
                min={1}
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm"
                value={epForm.episode}
                onChange={(e) =>
                  setEpForm((f) => ({ ...f, episode: Number(e.target.value) }))
                }
              />
            </div>
            <input
              type="file"
              accept="video/*"
              onChange={(e) =>
                setEpForm((f) => ({
                  ...f,
                  file: e.target.files?.[0] || null,
                }))
              }
            />
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-emerald-800 px-3 py-2 text-sm text-white disabled:opacity-50"
            >
              Upload & find moments
            </button>
          </form>
        </div>

        <section className="mt-10">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
            Episodes
          </h2>
          <div className="overflow-hidden rounded-xl bg-white/70 ring-1 ring-zinc-200">
            <table className="w-full text-left text-sm">
              <thead className="bg-zinc-100 text-zinc-500">
                <tr>
                  <th className="px-3 py-2">Name</th>
                  <th className="px-3 py-2">S/E</th>
                  <th className="px-3 py-2">Duration</th>
                  <th className="px-3 py-2">Suggest</th>
                </tr>
              </thead>
              <tbody>
                {episodes.map((ep) => (
                  <tr key={ep.id} className="border-t border-zinc-200">
                    <td className="px-3 py-2">{ep.name}</td>
                    <td className="px-3 py-2">
                      S{ep.season}E{ep.episode_number}
                    </td>
                    <td className="px-3 py-2">
                      {ep.duration_s ? `${Math.round(ep.duration_s)}s` : "—"}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          ep.suggest_status === "ready"
                            ? "text-emerald-700"
                            : ep.suggest_status === "failed"
                              ? "text-red-700"
                              : "text-amber-700"
                        }
                      >
                        {ep.suggest_status || "—"}
                      </span>
                    </td>
                  </tr>
                ))}
                {episodes.length === 0 ? (
                  <tr>
                    <td className="px-3 py-4 text-zinc-500" colSpan={4}>
                      No episodes yet.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>

        {status ? (
          <p className="mt-4 whitespace-pre-wrap text-sm text-zinc-700">{status}</p>
        ) : null}
      </main>
    </div>
  )
}
