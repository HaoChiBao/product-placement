import Link from "next/link"

import { cn } from "@/lib/utils"

/** Unskippable logo + wordmark, linking home. */
export function BrandMark({ className, size = 34 }: { className?: string; size?: number }) {
  return (
    <Link href="/" aria-label="Unskippable home" className={cn("flex shrink-0 items-center gap-2", className)}>
      <img src="/logo.png" alt="" width={size} height={size} className="block" style={{ width: size, height: size }} />
      <span className="font-brand text-[26px] uppercase leading-none tracking-[0.03em] text-[var(--account)] translate-y-[1px]">
        Unskippable
      </span>
    </Link>
  )
}
