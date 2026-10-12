import { useState } from "react"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDateTime } from "@/lib/utils"
import { PART_LABELS, gradeTone, priorityTone } from "./labels"
import type { EnquiryDetail } from "./types"

const TONES = {
  green: "bg-green-100 text-green-500",
  teal: "bg-teal-100 text-teal-600",
  amber: "bg-amber-100 text-amber-500",
  neutral: "bg-surface-2 text-ink-300",
}

/** The priority number, 0 to 100. */
export function PriorityBadge({ score, big }: { score: number | null | undefined; big?: boolean }) {
  return (
    <span title="Priority, from Fit, Intent and Risk"
      className={cn("inline-block text-center font-mono font-semibold rounded",
        big ? "min-w-[52px] text-[22px] px-2 py-1" : "min-w-[34px] text-[12px] px-1.5 py-[2px]",
        TONES[priorityTone(score)])}>
      {score ?? "–"}
    </span>
  )
}

/** Lead scoring v1 (F1.7, N15 A): three grades, the priority and the top three reasons.
 *  "How it was worked out" shows every signal with its points and weight. */
export function ScoreCard({ e }: { e: EnquiryDetail }) {
  const [open, setOpen] = useState(false)
  const parts = e.score_parts
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle>Priority</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-2 text-[13px]">
        {!parts ? <p className="text-ink-300">Not worked out yet.</p> : (
          <>
            <div className="flex items-center gap-3">
              <PriorityBadge score={e.score} big />
              <div className="flex flex-wrap gap-1">
                {(["fit", "intent", "risk"] as const).map((p) => (
                  <Tag key={p} variant={gradeTone(p, parts[p].grade)}>{PART_LABELS[p]}: {parts[p].grade}</Tag>
                ))}
              </div>
            </div>
            <ul className="space-y-1">
              {e.score_reasons.map((r, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className={r.up ? "text-green-500" : "text-red-500"} aria-label={r.up ? "raises it" : "lowers it"}>{r.up ? "▲" : "▼"}</span>
                  <span className="text-ink-700">{r.text}</span>
                </li>
              ))}
            </ul>
            <button type="button" className="text-[12px] text-teal-600 hover:underline" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? "Hide how it was worked out" : "How it was worked out"}
            </button>
            {open && (
              <div className="space-y-2">
                {(["fit", "intent", "risk"] as const).map((p) => (
                  <div key={p}>
                    <p className="font-semibold text-ink-700">{PART_LABELS[p]} {parts[p].points}/100</p>
                    <ul className="text-[12px] text-ink-500 space-y-0.5">
                      {parts[p].signals.map((s) => (
                        <li key={s.key}>{s.label}: <span className="font-mono">{s.points}</span> (weight {s.weight}) · {s.text}</li>
                      ))}
                    </ul>
                  </div>
                ))}
                <p className="text-[11.5px] text-ink-300">
                  Priority comes from Fit and Intent together, then Risk lowers it. The weights are under Setup → Scoring.
                  {e.scored_at && ` Worked out ${formatDateTime(e.scored_at)}.`}
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  )
}
