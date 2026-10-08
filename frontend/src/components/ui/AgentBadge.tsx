import * as React from "react"
import { cn } from "@/lib/utils"

export interface AgentBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  label?: string
}

export function AgentBadge({ label, className, ...props }: AgentBadgeProps) {
  return (
    <span 
      className={cn(
        "inline-flex items-center gap-[5px] font-mono font-medium text-[10.5px] text-teal-600 tracking-wide",
        className
      )}
      {...props}
    >
      <span className="text-[8px] leading-none">◆</span>
      {label ? `AGENT · ${label}` : 'AGENT'}
    </span>
  )
}
