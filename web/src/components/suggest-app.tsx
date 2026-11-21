"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { MomentCard } from "@/components/moment-card"
import { HaloButton } from "@/components/ui/halo-button"
import { HaloDropzone } from "@/components/ui/halo-dropzone"
import { HaloField, HaloFieldDescription, HaloFieldLabel } from "@/components/ui/halo-field"
import { HaloInput } from "@/components/ui/halo-input"
import { HaloProgress } from "@/components/ui/halo-progress"
import {
  estimateSwapCostUsd,
  type JobStatus,
  type SuggestRun,
} from "@/lib/types"

type SwapResult = {
  original_url: string
  result_url: string
  download_url: string
  estimated_cost_usd: number
  billed_duration_s?: number
  elapsed_s?: number
  backend?: string
  mock?: boolean
  target?: string
}

async function pollJob(
  jobId: string,
  opts?: {
    signal?: AbortSignal
    onProgress?: (text: string) => void
    timeoutMs?: number
  }
): Promise<JobStatus> {
  const timeoutMs = opts?.timeoutMs ?? 20 * 60 * 1000
  const t0 = Date.now()
  for (;;) {
    if (opts?.signal?.aborted) throw new Error("aborted")
    if (Date.now() - t0 > timeoutMs) {
      throw new Error(`job timed out after ${Math.round(timeoutMs / 1000)}s`)
    }
    const res = await fetch(`/api/jobs/${jobId}`, { signal: opts?.signal })
    const data = (await res.json()) as JobStatus & { error?: string }
    if (!res.ok) throw new Error(data.error || res.statusText)
    opts?.onProgress?.(data.progress || data.status)
    if (data.status === "completed" || data.status === "failed") return data
    await new Promise((r) => setTimeout(r, 1200))
  }
}

export function SuggestApp() {
  const [file, setFile] = useState<File | null>(null)
  const [path, setPath] = useState("")
  const [n, setN] = useState(5)
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [run, setRun] = useState<SuggestRun | null>(null)
  const [selected, setSelected] = useState(0)

  const [productFile, setProductFile] = useState<File | null>(null)
  const [productPath, setProductPath] = useState("assets/samples/soda_can.png")
  const [target, setTarget] = useState("")
  const [confirmCost, setConfirmCost] = useState(false)
  const [swapping, setSwapping] = useState(false)
  const [swapProgress, setSwapProgress] = useState<string | null>(null)
  const [swapResult, setSwapResult] = useState<SwapResult | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const selectedMoment = run?.moments[selected] ?? null
  const estimatedCost = useMemo(() => {
    if (!selectedMoment) return estimateSwapCostUsd(4.96)
    const dur = Math.min(4.96, Math.max(selectedMoment.end_s - selectedMoment.start_s, 2))
    return estimateSwapCostUsd(dur)
  }, [selectedMoment])

  useEffect(() => {
    if (selectedMoment) {
      setTarget(selectedMoment.whats_there || "")
      setConfirmCost(false)
      setSwapResult(null)
    }
  }, [selectedMoment])

  const onFiles = useCallback((files: File[]) => {
    setFile(files[0] ?? null)
    setError(null)
  }, [])

  const onProductFiles = useCallback((files: File[]) => {
    setProductFile(files[0] ?? null)
    setError(null)
  }, [])

  async function runSuggest(opts?: { path?: string; n?: number; file?: File | null }) {
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setLoading(true)
    setError(null)
    setRun(null)
    setSwapResult(null)
    setProgress("Starting suggest job…")
    try {
      const form = new FormData()
      const nn = opts?.n ?? n
      form.set("n", String(nn))
      const p = (opts?.path ?? path).trim()
      const f = opts?.file !== undefined ? opts.file : file
      if (p) form.set("path", p)
      else if (f) form.set("file", f)
      else throw new Error("Drop a video or enter a server path")

      const res = await fetch("/api/jobs/suggest", { method: "POST", body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || res.statusText)
      const jobId = data.jobId as string
      setProgress(data.progress || "Queued")

      const job = await pollJob(jobId, {
        signal: ac.signal,
        onProgress: (text) => setProgress(text),
        timeoutMs: 15 * 60 * 1000,
      })
      if (job.status === "failed") throw new Error(job.error || "suggest failed")

      const result = (job.result || null) as SuggestRun | null
      if (!result?.moments) throw new Error("suggest returned no moments")
      setRun(result)
      setSelected(0)
      setProgress(null)
    } catch (e) {
      if ((e as Error).message !== "aborted") {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoading(false)
    }
  }

  async function loadDemo() {
    setPath("assets/samples/hand_held_cup.mp4")
    setFile(null)
    setN(3)
    await runSuggest({ path: "assets/samples/hand_held_cup.mp4", n: 3, file: null })
  }

  async function runSwap() {
    if (!run || !selectedMoment) return
    if (!confirmCost) {
      setError("Confirm the estimated Seedance cost before running a swap.")
      return
    }
    if (!productFile && !productPath.trim()) {
      setError("Upload a product image or pick the sample path.")
      return
    }

    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setSwapping(true)
    setError(null)
    setSwapResult(null)
    setSwapProgress("Starting swap job…")
    try {
      const form = new FormData()
      form.set("runId", run.runId)
      form.set("momentIndex", String(selected))
      form.set("target", target || selectedMoment.whats_there)
      form.set("confirm", "true")
      if (productFile) form.set("product", productFile)
      else form.set("productPath", productPath.trim())

      const res = await fetch("/api/jobs/swap", { method: "POST", body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || res.statusText)
      const jobId = data.jobId as string
      setSwapProgress(
        data.mock
          ? `Mock swap queued · est. $${Number(data.estimated_cost_usd).toFixed(2)} (not charged)`
          : `Swap queued · est. $${Number(data.estimated_cost_usd).toFixed(2)}`
      )

      const job = await pollJob(jobId, {
        signal: ac.signal,
        onProgress: (text) => setSwapProgress(text),
        timeoutMs: 25 * 60 * 1000,
      })
      if (job.status === "failed") throw new Error(job.error || "swap failed")
      const result = job.result as SwapResult | undefined
      if (!result?.result_url) throw new Error("swap completed without result video")
      setSwapResult(result)
      setSwapProgress(null)
      setConfirmCost(false)
    } catch (e) {
      if ((e as Error).message !== "aborted") {
        setError(e instanceof Error ? e.message : String(e))
        setSwapProgress(null)
      }
    } finally {
      setSwapping(false)
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
            product-placement
          </p>
          <h1 className="font-[family-name:var(--font-display)] text-4xl leading-[1.05] tracking-tight text-[var(--ink)] sm:text-5xl">
            Suggest · swap
          </h1>
          <p className="max-w-xl text-base leading-relaxed text-[var(--ink-soft)]">
            Upload a cut, pick a face-free moment, drop a product, then run a
            crop-and-stitch Seedance 2.5 swap. Jobs poll in the background — swaps
            are capped at 5&nbsp;s / 720p and need an explicit cost confirm.
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
              onClick={() => runSuggest()}
              disabled={loading || swapping}
            >
              Run suggest
            </HaloButton>
            <HaloButton
              type="button"
              translucent={false}
              isLoading={loading}
              loadingText="Loading…"
              onClick={loadDemo}
              disabled={loading || swapping}
            >
              Demo: hand-held cup
            </HaloButton>
          </div>
        </div>
      </section>

      {(loading || progress) && (
        <div className="space-y-2 rounded-2xl border border-black/5 bg-white/80 p-4">
          <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
            {progress || "Scanning shots · faces · props"}
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

      {run && selectedMoment && (
        <section className="space-y-5 rounded-[28px] border border-black/5 bg-white/75 p-6 backdrop-blur">
          <div>
            <h2 className="font-[family-name:var(--font-display)] text-2xl text-[var(--ink)]">
              Swap moment #{selected + 1}
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-[var(--ink-soft)]">
              Crop the placement box → hold ≈5&nbsp;s at 1280×720 → Seedance 2.5 →
              feather stitch. Server cap: 5&nbsp;s input, 720p only.
            </p>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <HaloDropzone
              accept="image/*,.png,.jpg,.jpeg,.webp"
              multiple={false}
              onFiles={onProductFiles}
              emptyLabel="Drop a product image"
              formatHint="PNG with alpha works best"
              successLabel={productFile ? productFile.name : "Ready"}
              className="min-h-[180px]"
            />

            <div className="flex flex-col gap-4">
              <HaloField>
                <HaloFieldLabel>Or sample product path</HaloFieldLabel>
                <HaloInput
                  value={productPath}
                  onChange={(e) => setProductPath(e.target.value)}
                  placeholder="assets/samples/soda_can.png"
                />
              </HaloField>
              <HaloField>
                <HaloFieldLabel>Target object</HaloFieldLabel>
                <HaloInput
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  placeholder="wine glass"
                />
              </HaloField>

              <div className="rounded-2xl border border-amber-200/80 bg-amber-50/90 px-4 py-3 text-sm text-amber-950">
                <p className="font-medium">
                  Estimated Seedance cost: ${estimatedCost.toFixed(2)}
                </p>
                <p className="mt-1 text-amber-900/80">
                  ~$0.3264/s at 720p · billed as ceil(seconds) · ~$1.63 for a 5&nbsp;s
                  hold. Set <code className="font-mono">MOCK_HIGGSFIELD=1</code> to
                  skip the paid call.
                </p>
                <label className="mt-3 flex cursor-pointer items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={confirmCost}
                    onChange={(e) => setConfirmCost(e.target.checked)}
                  />
                  <span>
                    I understand this may charge Higgsfield and want to run the swap
                    on moment #{selected + 1}.
                  </span>
                </label>
              </div>

              <HaloButton
                type="button"
                isLoading={swapping}
                loadingText="Swapping…"
                onClick={runSwap}
                disabled={swapping || loading || !confirmCost}
              >
                Run crop-and-stitch swap
              </HaloButton>
            </div>
          </div>

          {(swapping || swapProgress) && (
            <div className="space-y-2 rounded-2xl border border-black/5 bg-white/80 p-4">
              <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                {swapProgress || "Running swap…"}
              </p>
              <HaloProgress value={null} />
            </div>
          )}

          {swapResult && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <h3 className="font-[family-name:var(--font-display)] text-xl text-[var(--ink)]">
                    Result
                  </h3>
                  <p className="font-mono text-xs text-muted-foreground">
                    {swapResult.backend || "seedance"}
                    {swapResult.mock ? " · mock" : ""}
                    {swapResult.elapsed_s != null
                      ? ` · ${swapResult.elapsed_s.toFixed(1)}s wall`
                      : ""}
                    {` · est. $${Number(swapResult.estimated_cost_usd).toFixed(2)}`}
                  </p>
                </div>
                <a
                  href={swapResult.download_url}
                  className="inline-flex h-10 items-center justify-center rounded-full bg-[var(--ink)] px-5 text-sm font-medium text-white transition hover:opacity-90"
                >
                  Download result
                </a>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Original moment
                  </p>
                  <video
                    className="aspect-video w-full rounded-xl bg-black object-contain"
                    src={swapResult.original_url}
                    controls
                    playsInline
                  />
                </div>
                <div className="space-y-2">
                  <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Swapped
                  </p>
                  <video
                    className="aspect-video w-full rounded-xl bg-black object-contain"
                    src={swapResult.result_url}
                    controls
                    playsInline
                  />
                </div>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
