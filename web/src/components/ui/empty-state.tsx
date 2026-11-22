import { cn } from "@/lib/utils"

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-7 py-12 text-center text-muted-foreground",
        className
      )}
    >
      <b className="font-semibold text-foreground">{title}</b>
      {description ? <span className="max-w-[42ch] text-sm">{description}</span> : null}
      {action}
    </div>
  )
}

export function Skeleton({
  className,
  style,
}: {
  className?: string
  style?: React.CSSProperties
}) {
  return (
    <div
      style={style}
      className={cn(
        "h-3 rounded animate-shimmer bg-[linear-gradient(90deg,var(--muted),var(--border),var(--muted))] bg-[length:200%_100%]",
        className
      )}
    />
  )
}

export function Toast({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed bottom-5 left-1/2 z-[60] -translate-x-1/2 rounded-md bg-primary px-4 py-2.5 text-[13px] text-primary-foreground shadow-soft">
      {children}
    </div>
  )
}
