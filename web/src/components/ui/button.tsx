import { cva, type VariantProps } from "class-variance-authority"
import Link from "next/link"
import type { ButtonHTMLAttributes, ComponentProps } from "react"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium transition-all duration-150 ease-out disabled:opacity-45 disabled:cursor-not-allowed disabled:pointer-events-none",
  {
    variants: {
      variant: {
        primary:
          "bg-primary text-primary-foreground border border-primary hover:opacity-90",
        secondary:
          "bg-card text-foreground border border-border hover:border-faint",
        ghost:
          "bg-transparent text-foreground border border-transparent hover:bg-muted",
        stage:
          "bg-stage-2 text-stage-ink border border-stage-line hover:border-stage-muted",
        cinema:
          "bg-white text-[#0a0c0d] border border-transparent hover:bg-[#e8ebe9]",
        "cinema-ghost":
          "bg-white/8 text-foreground border border-border hover:bg-white/14",
      },
      size: {
        sm: "rounded-sm px-2.5 py-1.5 text-[12.5px]",
        md: "rounded-sm px-3.5 py-2.5 text-sm",
        lg: "rounded-sm px-[18px] py-3 text-[15px]",
      },
    },
    defaultVariants: {
      variant: "secondary",
      size: "md",
    },
  }
)

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
}

type ButtonLinkProps = ComponentProps<typeof Link> & VariantProps<typeof buttonVariants>

export function ButtonLink({ className, variant, size, ...props }: ButtonLinkProps) {
  return <Link className={cn(buttonVariants({ variant, size }), className)} {...props} />
}

export { buttonVariants }
