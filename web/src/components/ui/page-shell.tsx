import { cn } from "@/lib/utils"

export function PageShell({
  className,
  children,
  narrow,
}: {
  className?: string
  children: React.ReactNode
  narrow?: boolean
}) {
  return (
    <main
      className={cn(
        "mx-auto w-full px-4 pb-16 pt-7 sm:px-6",
        narrow ? "max-w-[1120px]" : "max-w-shell",
        className
      )}
    >
      {children}
    </main>
  )
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string
  title: React.ReactNode
  description?: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-6">
      <div className="min-w-0">
        {eyebrow ? <div className="eyebrow">{eyebrow}</div> : null}
        <div className={eyebrow ? "mt-1.5" : undefined}>{title}</div>
        {description ? (
          <div className="mt-2 max-w-[46ch] text-muted-foreground">{description}</div>
        ) : null}
      </div>
      {action}
    </div>
  )
}
