import { Link } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Tag } from "@/components/ui/Tag"
import { BAND_LABELS, effectTone } from "@/features/steward/labels"
import type { DuplicateCheck } from "@/features/steward/types"

/** Live result of POST /accounts/check while a form is being filled in. */
export function DuplicatePanel({ check, excludeId }: { check: DuplicateCheck | undefined; excludeId?: string }) {
  const matches = (check?.matches ?? []).filter((m) => m.id !== excludeId)
  return (
    <Card>
      <CardHeader><CardTitle>Checks</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-[13px]">
        {!check && <p className="text-ink-300">Type a name to check for existing records.</p>}
        {check?.problems.map((p) => <p key={p} className="text-red-500">{p}</p>)}
        {check?.warnings.map((w) => <p key={w} className="text-amber-500">{w}</p>)}
        {check && matches.length === 0 && check.problems.length === 0 && (
          <p className="text-green-500">No existing record looks like this one.</p>
        )}
        {matches.length > 0 && (
          <div className="space-y-2">
            <p className="font-semibold text-ink-700">Possible existing records</p>
            {matches.map((m) => (
              <div key={m.id} className="rounded-md border border-line p-2.5 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <Link to={`/accounts/${m.id}`} className="text-teal-600 font-medium hover:underline">{m.name}</Link>
                  <Tag variant={m.band === "auto_merge" ? "red" : "gold"}>{BAND_LABELS[m.band]}</Tag>
                </div>
                <div className="text-ink-300 text-[12px]">{[m.city, m.gstin].filter(Boolean).join(" · ")}</div>
                <div className="flex flex-wrap gap-1">
                  {m.reasons.filter((r) => r.effect !== "info").map((r) => (
                    <Tag key={r.signal} variant={effectTone(r.effect)} title={r.detail}>{r.detail}</Tag>
                  ))}
                </div>
              </div>
            ))}
            {matches.some((m) => m.band === "auto_merge") && (
              <p className="text-[12px] text-ink-500">
                If you save anyway, the Data Steward will merge it into the existing record.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
