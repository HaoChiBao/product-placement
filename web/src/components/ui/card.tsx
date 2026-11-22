import { cn } from "@/lib/utils"

export function Panel({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={cn("rounded-lg border border-border bg-card shadow-soft", className)}>
      {children}
    </div>
  )
}

export function Card({
  className,
  children,
  as: Comp = "div",
  ...props
}: {
  className?: string
  children: React.ReactNode
  as?: "div" | "button" | "a"
} & React.HTMLAttributes<HTMLElement>) {
  return (
    <Comp className={cn("min-w-0 text-left", className)} {...props}>
      {children}
    </Comp>
  )
}
