import { cn } from "@/lib/utils"
import { TONE_CLASS, timerText, timerTone } from "./labels"
import type { Timer } from "./types"

/** A timer as a coloured chip: green in time, amber near the end, red late. */
export function TimerChip({ t, className }: { t: Timer; className?: string }) {
  return (
    <span title={t.label} className={cn("inline-block font-mono text-[11px] font-semibold rounded px-2 py-[3px] whitespace-nowrap",
      TONE_CLASS[timerTone(t)], className)}>
      {timerText(t)}
    </span>
  )
}
