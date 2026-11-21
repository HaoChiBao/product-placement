"use client"

import { useCallback, useState } from "react"

import { MomentCard } from "@/components/moment-card"
import { HaloButton } from "@/components/ui/halo-button"
import { HaloDropzone } from "@/components/ui/halo-dropzone"
import { HaloField, HaloFieldDescription, HaloFieldLabel } from "@/components/ui/halo-field"
import { HaloInput } from "@/components/ui/halo-input"
import { HaloProgress } from "@/components/ui/halo-progress"
import type { SuggestRun } from "@/lib/runs"

export function SuggestApp() {
  const [file, setFile] = useState<File | null>(null)
  const [path, setPath] = useState("")
  const [n, setN] = useState(5)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [run, setRun] = useState<SuggestRun | null>(null)
  const [selected, setSelected] = useState(0)

  const onFiles = useCallback((files: File[]) => {
    setFile(files[0] ?? null)
    setError(null)
  }, [])

  async function runSuggest() {
    setLoading(true)
    setError(null)
    setRun(null)
    try {
      const form = new FormData()
      form.set("n", String(n))
      if (path.trim()) form.set("path", path.trim())
      else if (file) form.set("file", file)
      else throw new Error("Drop a video or enter a server path")

      const res = await fetch("/api/suggest", { method: "POST", body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || res.statusText)
      setRun(data as SuggestRun)
      setSelected(0)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  async function loadDemo() {
    setPath("assets/samples/hand_held_cup.mp4")
    setFile(null)
    setLoading(true)
    setError(null)
    setRun(null)
    try {
      const form = new FormData()
      form.set("n", "3")
      form.set("path", "assets/samples/hand_held_cup.mp4")
      const res = await fetch("/api/suggest", { method: "POST", body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || res.statusText)
      setRun(data as SuggestRun)
      setSelected(0)
      setN(3)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-10 px-4 py-10 sm:px-6 lg:px-8">
      <header className="relative overflow-hidden rounded-[28px] border border-black/5 bg-[linear-gradient(135deg,#e8f0f2_0%,#dfe8ec_48%,#d7e6e1_100%)] px-6 py-10 sm:px-10">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.4]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 18% 20%, rgba(47,158,122,0.22), transparent 42%), radial-gradient(circle at 82% 8%, rgba(35,70,100,0.16), transparent 36%), repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.02) 3px)",
          }}
        />
        <div className="relative max-w-2xl space-y-3">
          <p className="font-mono text-xs uppercase tracking-[0.22em] text-[var(--ink-soft)]">
            product-placement · suggest
          </p>
          <h1 className="font-[family-name:var(--font-display)] text-4xl leading-[1.05] tracking-tight text-[var(--ink)] sm:text-5xl">
            Find the moments
          </h1>
          <p className="max-w-xl text-base leading-relaxed text-[var(--ink-soft)]">
            Upload a cut or point at a path on this machine. We return 3–5 face-free
            ~5&nbsp;s windows with a placement box ready for crop-and-stitch swap.
          </p>
        </div>
      </header>

      <section className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <HaloDropzone
          accept="video/*,.mp4,.mov,.mkv,.webm"
          multiple={false}
          onFiles={onFiles}
          emptyLabel="Drop a video or browse"
          formatHint="MP4 / MOV / MKV — or use a server path on the right"
          successLabel={file ? file.name : "Ready"}
          showSuccessThumbnails={false}
          className="min-h-[220px]"
        />

        <div className="flex flex-col gap-4 rounded-[24px] border border-black/5 bg-white/70 p-5 backdrop-blur">
          <HaloField>
            <HaloFieldLabel>Server path</HaloFieldLabel>
            <HaloInput
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="assets/samples/hand_held_cup.mp4"
            />
            <HaloFieldDescription>
              Absolute path or repo-relative. Takes priority over an upload.
            </HaloFieldDescription>
          </HaloField>

          <HaloField>
            <HaloFieldLabel>How many moments</HaloFieldLabel>
            <HaloInput
              type="number"
              min={3}
              max={5}
              value={n}
              onChange={(e) => setN(Number(e.target.value))}
            />
            <HaloFieldDescription>Clamped to 3–5.</HaloFieldDescription>
          </HaloField>

          <div className="mt-auto flex flex-wrap gap-3 pt-2">
            <HaloButton
              type="button"
              isLoading={loading}
              loadingText="Suggesting…"
              onClick={runSuggest}
              disabled={loading}
            >
              Run suggest
            </HaloButton>
            <HaloButton
              type="button"
              translucent={false}
              isLoading={loading}
              loadingText="Loading…"
              onClick={loadDemo}
              disabled={loading}
            >
              Demo: hand-held cup
            </HaloButton>
          </div>
        </div>
      </section>

      {loading && (
        <div className="space-y-2 rounded-2xl border border-black/5 bg-white/80 p-4">
          <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            Scanning shots · faces · props
          </p>
          <HaloProgress value={null} />
        </div>
      )}

      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          {error}
        </div>
      )}

      {run && (
        <section className="space-y-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
                {run.moments.length} picks
              </h2>
              <p className="font-mono text-xs text-muted-foreground">
                run {run.runId}
                {run.elapsed_s != null ? ` · ${run.elapsed_s.toFixed(1)}s` : ""} ·{" "}
                {run.duration_s?.toFixed?.(1) ?? "?"}s source
              </p>
            </div>
          </div>

          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {run.moments.map((m, i) => (
              <MomentCard
                key={`${m.start_s}-${i}`}
                moment={m}
                index={i}
                selected={selected === i}
                onSelect={() => setSelected(i)}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
