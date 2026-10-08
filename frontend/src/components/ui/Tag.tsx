import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const tagVariants = cva(
  "inline-block font-mono text-[10px] tracking-[0.06em] px-2 py-[3px] rounded uppercase font-semibold",
  {
    variants: {
      variant: {
        teal: "bg-[#e3f1f2] text-[#0a5b63]",
        gold: "bg-[#f8f1dc] text-[#8a6d10]",
        green: "bg-[#e2f3ea] text-[#1e8e5a]",
        red: "bg-[#fbe9e7] text-[#c0392b]",
        amber: "bg-[#fdf1de] text-[#c77700]",
        neutral: "bg-surface-2 text-ink-500",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  }
)

export interface TagProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof tagVariants> {}

export function Tag({ className, variant, ...props }: TagProps) {
  return (
    <span className={cn(tagVariants({ variant }), className)} {...props} />
  )
}
