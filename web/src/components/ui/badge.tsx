import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-medium",
  {
    variants: {
      variant: {
        accent: "bg-accent-soft text-accent-foreground",
        warning: "bg-warning-soft text-warning",
        danger: "bg-destructive-soft text-destructive",
        muted: "bg-muted text-muted-foreground",
        overlay: "bg-black/70 text-white rounded-[5px] px-2 py-1 text-[11px]",
        live: "bg-accent text-accent-foreground rounded-[5px] px-2 py-1 text-[11px]",
      },
    },
    defaultVariants: { variant: "accent" },
  }
)

export function Badge({
  className,
  variant,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props}>
      {children}
    </span>
  )
}

export function StatusDot({ className }: { className?: string }) {
  return <span className={cn("size-1.5 shrink-0 rounded-full bg-current", className)} />
}
