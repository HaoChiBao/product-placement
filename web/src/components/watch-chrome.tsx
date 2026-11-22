import Link from "next/link"

import { BrandMark } from "@/components/brand-mark"
import { cn } from "@/lib/utils"

export function WatchChrome({
  current = "watch",
  meta,
}: {
  current?: "watch" | "play"
  meta?: string
}) {
  return (
    <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-border bg-background/78 px-4 py-3.5 backdrop-blur-[14px] sm:px-10">
      <BrandMark />
      <nav className="ml-2 hidden items-center gap-1 md:flex" aria-label="Watch">
        <Link
          href="/watch"
          aria-current={current === "watch" ? "page" : undefined}
          className={cn(
            "rounded-sm px-2.5 py-1.5 text-[13.5px] font-medium text-muted-foreground transition-colors duration-150",
            current === "watch" && "bg-card text-foreground"
          )}
        >
          Watch
        </Link>
        <Link
          href="/videos"
          className="rounded-sm px-2.5 py-1.5 text-[13.5px] font-medium text-muted-foreground transition-colors duration-150 hover:bg-card hover:text-foreground"
        >
          Brand flow
        </Link>
      </nav>
      <div className="flex-1" />
      {meta ? <div className="text-[12.5px] text-faint">{meta}</div> : null}
    </header>
  )
}
