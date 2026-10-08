import * as React from "react"
import { cn } from "@/lib/utils"

export const inputClass =
  "w-full rounded-md border border-input bg-surface-1 px-3 py-2 text-[13.5px] shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-teal-500 disabled:opacity-60"

/** `group` renders a <div> instead of a <label>: use it around widgets with their own
 *  buttons (pickers), because a click inside a <label> is re-sent to its first control. */
export function Field({ label, hint, children, className, group }: {
  label: string; hint?: React.ReactNode; children: React.ReactNode; className?: string; group?: boolean
}) {
  const Tag = group ? "div" : "label"
  return (
    <Tag className={cn("block space-y-1.5", className)}>
      <span className="block text-[12px] font-semibold text-ink-700">{label}</span>
      {children}
      {hint && <span className="block text-[11.5px] text-ink-300">{hint}</span>}
    </Tag>
  )
}

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(inputClass, className)} {...props} />
)
Input.displayName = "Input"

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => <select ref={ref} className={cn(inputClass, className)} {...props} />
)
Select.displayName = "Select"
