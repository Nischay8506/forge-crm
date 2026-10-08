import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { KpiCard } from "@/components/ui/KpiCard"
import { ScoreBadge } from "@/components/ui/ScoreBadge"
import { ErrorText, Loading } from "@/components/ui/States"
import { cn } from "@/lib/utils"
import type { HygieneBucket, HygieneDashboard } from "./types"

const BUCKETS = [
  ["90-100", "bg-green-500"], ["70-89", "bg-teal-500"], ["50-69", "bg-amber-500"], ["0-49", "bg-red-500"],
] as const

function Distribution({ bucket }: { bucket: HygieneBucket }) {
  const total = Object.values(bucket.distribution).reduce((a, b) => a + b, 0)
  return (
    <div className="space-y-2">
      <div className="flex h-3 rounded overflow-hidden bg-surface-2">
        {BUCKETS.map(([k, color]) => {
          const n = bucket.distribution[k] ?? 0
          return n > 0 ? <div key={k} className={color} style={{ width: `${(100 * n) / total}%` }} title={`${k}: ${n}`} /> : null
        })}
      </div>
      <div className="flex gap-4 text-[12px] text-ink-500">
        {BUCKETS.map(([k, color]) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className={cn("w-2 h-2 rounded-sm", color)} />{k}: {bucket.distribution[k] ?? 0}
          </span>
        ))}
      </div>
    </div>
  )
}

function Section({ title, bucket, linkRecords, extra }: {
  title: string; bucket: HygieneBucket; linkRecords: boolean; extra?: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <KpiCard value={bucket.average_score ?? "–"} label="Average score" />
          <KpiCard value={bucket.active} label="Active records" />
          <KpiCard value={bucket.open_duplicate_candidates} label="Possible duplicates to review" />
          <KpiCard value={bucket.with_invalid_values} label="With invalid values" />
          <KpiCard value={bucket.merged} label="Merged away" />
        </div>
        {extra}
        <Distribution bucket={bucket} />
        {bucket.lowest.length > 0 && (
          <div>
            <p className="text-[12px] font-semibold text-ink-700 mb-1">Lowest scores</p>
            <ul className="divide-y divide-line text-[13px]">
              {bucket.lowest.map((r) => (
                <li key={r.id} className="py-1.5 flex items-center justify-between">
                  {linkRecords ? <Link to={`/accounts/${r.id}`} className="text-teal-600 hover:underline">{r.name}</Link> : <span>{r.name}</span>}
                  <ScoreBadge score={r.score} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export function HygienePage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["hygiene"],
    queryFn: async () => (await api.get<HygieneDashboard>("/steward/hygiene")).data,
  })
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load data hygiene" />
  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-ink-500">
        Each record scores out of 100: filled fields (40), valid values (30), verified in the last year (20)
        and no open duplicates (10).
      </p>
      <Section title="Accounts" bucket={data.accounts} linkRecords
        extra={data.accounts.entities_without_gstin > 0 && (
          <p className="text-[12.5px] text-amber-500">{data.accounts.entities_without_gstin} legal entities have no GSTIN.</p>
        )} />
      <Section title="Contacts" bucket={data.contacts} linkRecords={false} />
    </div>
  )
}
