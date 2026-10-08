import { cn } from "@/lib/utils"

/** Hygiene score 0-100: green from 90, teal from 70, amber from 50, red below. */
export function scoreTone(score: number | null | undefined): "green" | "teal" | "amber" | "red" | "neutral" {
  if (score === null || score === undefined) return "neutral"
  if (score >= 90) return "green"
  if (score >= 70) return "teal"
  if (score >= 50) return "amber"
  return "red"
}

const TONES = {
  green: "bg-green-100 text-green-500",
  teal: "bg-teal-100 text-teal-600",
  amber: "bg-amber-100 text-amber-500",
  red: "bg-red-100 text-red-500",
  neutral: "bg-surface-2 text-ink-300",
}

export function ScoreBadge({ score, className }: { score: number | null | undefined; className?: string }) {
  return (
    <span title="Data hygiene score"
      className={cn("inline-block min-w-[34px] text-center font-mono text-[11px] font-semibold rounded px-1.5 py-[2px]",
        TONES[scoreTone(score)], className)}>
      {score ?? "–"}
    </span>
  )
}
