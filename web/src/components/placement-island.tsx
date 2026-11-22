"use client"

import { useEffect } from "react"

import {
  DynamicContainer,
  DynamicDescription,
  DynamicIsland,
  DynamicIslandProvider,
  DynamicTitle,
  useDynamicIslandSize,
} from "@/components/ui/dynamic-island"

export type IslandStatus = "pending" | "generating" | "ready" | "rejected" | "failed"

type Props = {
  status: IslandStatus
  index: number
  total: number
  what: string
  message?: string
}

function IslandBody({ status, index, total, what, message }: Props) {
  const { state, setSize } = useDynamicIslandSize()

  useEffect(() => {
    setSize(status === "generating" || status === "failed" ? "long" : "compact")
  }, [status, setSize])

  if (state.size === "long" && status === "generating") {
    return (
      <DynamicContainer className="flex h-full w-full items-center gap-3 px-5 text-left text-white">
        <span className="size-7 shrink-0 animate-spin rounded-full border-[2.5px] border-accent border-t-transparent" />
        <div className="min-w-0">
          <DynamicTitle className="truncate text-sm font-semibold">Placing your product</DynamicTitle>
          <DynamicDescription className="truncate text-xs text-white/60">
            Spot {index} of {total} · {what} · usually under a minute
          </DynamicDescription>
        </div>
      </DynamicContainer>
    )
  }

  if (state.size === "long" && status === "failed") {
    return (
      <DynamicContainer className="flex h-full w-full items-center gap-3 px-5 text-left text-white">
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[#e5484d] text-sm font-bold">!</span>
        <div className="min-w-0">
          <DynamicTitle className="truncate text-sm font-semibold">Generation failed</DynamicTitle>
          <DynamicDescription className="line-clamp-2 text-xs text-white/60">{message}</DynamicDescription>
        </div>
      </DynamicContainer>
    )
  }

  const dot =
    status === "ready" ? "bg-success" : status === "rejected" ? "bg-[#e5484d]" : "bg-white/40"
  const label =
    status === "ready" ? "Placement ready" : status === "rejected" ? "Rejected · original kept" : "Awaiting your call"

  return (
    <DynamicContainer className="flex h-full w-full items-center justify-between gap-3 px-4 text-white">
      <DynamicDescription className="flex items-center gap-2 text-xs text-white/70">
        <span className={`size-2 rounded-full ${dot}`} />
        {index}/{total}
      </DynamicDescription>
      <DynamicTitle className="truncate text-xs font-semibold">{label}</DynamicTitle>
    </DynamicContainer>
  )
}

/** iPhone-style status pill floating over the review player. */
export function PlacementIsland(props: Props) {
  return (
    <div className="pointer-events-none absolute inset-x-0 top-3 z-10 flex justify-center">
      <DynamicIslandProvider initialSize="compact">
        <DynamicIsland id="placement-island">
          <IslandBody {...props} />
        </DynamicIsland>
      </DynamicIslandProvider>
    </div>
  )
}
