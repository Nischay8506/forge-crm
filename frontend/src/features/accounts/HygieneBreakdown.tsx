import { ScoreBadge } from "@/components/ui/ScoreBadge"
import { fieldLabel } from "@/features/steward/labels"
import type { HygieneDetails } from "@/features/steward/types"

function Bar({ label, points, of, children }: { label: string; points: number; of: number; children?: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[12px]">
        <span className="font-semibold text-ink-700">{label}</span>
        <span className="font-mono text-ink-500">{points} / {of}</span>
      </div>
      <div className="h-1.5 rounded bg-surface-2 overflow-hidden">
        <div className="h-full bg-teal-500" style={{ width: `${(100 * points) / of}%` }} />
      </div>
      {children && <div className="text-[12px] text-ink-500">{children}</div>}
    </div>
  )
}

const list = (fields: string[]) => fields.map(fieldLabel).join(", ")

export function HygieneBreakdown({ details }: { details: HygieneDetails | null }) {
  if (!details) return <p className="text-ink-300 text-[13px]">Not scored yet.</p>
  const { completeness: c, validity: v, freshness: f, duplicates: d } = details
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <ScoreBadge score={details.score} className="text-[14px] px-2 py-1" />
        <span className="text-[12px] text-ink-300">out of 100</span>
      </div>
      <Bar label="Completeness" points={c.points} of={c.of}>
        {c.missing.length > 0 && <>Missing: {list(c.missing)}</>}
      </Bar>
      <Bar label="Valid values" points={v.points} of={v.of}>
        {v.problems.map((p) => <p key={p} className="text-red-500">{p}</p>)}
        {v.warnings.map((w) => <p key={w} className="text-amber-500">{w}</p>)}
      </Bar>
      <Bar label="Recently verified" points={f.points} of={f.of}>
        {f.tracked > 0 && <>{f.verified} of {f.tracked} filled fields verified in the last year</>}
        {f.unverified.length > 0 && <>; not verified: {list(f.unverified)}</>}
      </Bar>
      <Bar label="No open duplicates" points={d.points} of={d.of}>
        {d.open_candidates > 0 && <>{d.open_candidates} possible duplicate(s) waiting for review</>}
      </Bar>
    </div>
  )
}
