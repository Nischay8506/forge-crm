import * as React from "react"
import { cn } from "@/lib/utils"

export interface NameplateProps extends React.HTMLAttributes<HTMLSpanElement> {
  id: string
}

export function Nameplate({ id, className, ...props }: NameplateProps) {
  return (
    <span 
      className={cn(
        "font-mono text-[10.5px] bg-gradient-to-b from-[#F4F6F9] to-[#E6EAF0] border border-[#C7CFDA] rounded px-[9px] py-[3px] text-ink-700 relative tracking-[0.05em] inline-block font-semibold shadow-sm",
        className
      )} 
      {...props}
    >
      {/* Left rivet */}
      <span className="absolute top-1/2 left-[3px] w-[4px] h-[4px] rounded-full bg-[#AEB8C6] -translate-y-1/2" />
      {id}
      {/* Right rivet */}
      <span className="absolute top-1/2 right-[3px] w-[4px] h-[4px] rounded-full bg-[#AEB8C6] -translate-y-1/2" />
    </span>
  )
}
