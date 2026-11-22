import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react"

import { cn } from "@/lib/utils"

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-sm border border-input bg-background px-[11px] py-2.5 outline-none transition-shadow duration-150",
        "focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-soft)]",
        className
      )}
      {...props}
    />
  )
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full min-h-16 resize-y rounded-sm border border-input bg-background px-[11px] py-2.5 leading-normal outline-none transition-shadow duration-150",
        "focus:border-accent focus:shadow-[0_0_0_3px_var(--accent-soft)]",
        className
      )}
      {...props}
    />
  )
}

export function Label({
  className,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-[12.5px] font-medium", className)} {...props} />
}

export function Field({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>{children}</div>
}
