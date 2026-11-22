"use client"

import type { VideoSource } from "@/lib/catalog-client"

const OPTIONS: { value: VideoSource; label: string }[] = [
  { value: "test", label: "Test" },
  { value: "hosted", label: "Hosted" },
  { value: "all", label: "All" },
]

export function VideoSourceToggle({
  value,
  onChange,
  variant = "light",
}: {
  value: VideoSource
  onChange: (next: VideoSource) => void
  variant?: "light" | "watch"
}) {
  const root = variant === "watch" ? "watch-source-toggle" : "source-toggle"
  return (
    <div className={root} role="group" aria-label="Video source">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={value === opt.value ? "on" : undefined}
          aria-pressed={value === opt.value}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
