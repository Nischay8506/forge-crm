import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, inputClass } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import type { LegalHold, Retention } from "@/features/agents/types"

const ORDER = ["skeleton", "customer_money", "other", "sync_logs", "model_logs"]

const REMOVED: Record<string, string> = {
  customer_money: "money and message details",
  other: "other details",
  sync_logs: "ERP sync runs",
  llm_calls: "model-call logs",
  sync_runs: "ERP sync runs",
  skeleton_records: "skeleton records",
}

/** "96" → "8 years", "18" → "1 year 6 months". */
export function monthsLabel(m: number): string {
  if (!Number.isFinite(m) || m <= 0) return ""
  const y = Math.floor(m / 12)
  const r = m % 12
  const part = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`
  return [y ? part(y, "year") : "", r ? part(r, "month") : ""].filter(Boolean).join(" ")
}

function minimum(r: Retention, key: string): number {
  return key === "skeleton" ? r.minimums.skeleton : r.minimums.other
}

/** How long Forge keeps each kind of audit and log record, the law each period
 * follows, and who confirmed it (R5, Step 4 safeguard 4). */
export function RetentionPage() {
  const perms = usePermissions()
  const canManage = hasPermission(perms, "audit.manage")
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ["retention"],
    queryFn: async () => (await api.get<Retention>("/audit/retention")).data,
  })
  const holds = useQuery({
    queryKey: ["legal-holds", "all"],
    queryFn: async () => (await api.get<LegalHold[]>("/audit/legal-holds")).data,
  })
  const [periods, setPeriods] = useState<Record<string, string>>({})
  const [basis, setBasis] = useState<Record<string, string>>({})
  useEffect(() => {
    if (!data) return
    setPeriods(Object.fromEntries(Object.entries(data.periods).map(([k, v]) => [k, String(v)])))
    setBasis({ ...data.basis })
  }, [data])
  const save = useMutation({
    mutationFn: async () => (await api.put<Retention>("/audit/retention", {
      periods: Object.fromEntries(Object.entries(periods).map(([k, v]) => [k, Number(v)])), basis,
    })).data,
    onSuccess: (r) => qc.setQueryData(["retention"], r),
  })
  const [reviewer, setReviewer] = useState("")
  const [note, setNote] = useState("")
  const review = useMutation({
    mutationFn: async () => (await api.post<Retention>("/audit/retention/legal-review", {
      reviewed_by: reviewer, note: note || null })).data,
    onSuccess: (r) => { setReviewer(""); setNote(""); qc.setQueryData(["retention"], r) },
  })
  const run = useMutation({
    mutationFn: async () => (await api.post<{ removed: Record<string, number> }>("/audit/retention/run")).data,
  })

  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the retention settings" />

  const keys = ORDER.filter((k) => k in data.categories)
  const problems = keys.flatMap((k) => {
    const n = Number(periods[k])
    if (!Number.isInteger(n)) return [`${data.categories[k]}: a whole number of months`]
    if (n < minimum(data, k)) return [`${data.categories[k]}: at least ${minimum(data, k)} months`]
    if (n > 600) return [`${data.categories[k]}: at most 600 months`]
    return []
  })
  const changed = keys.some((k) => Number(periods[k]) !== data.periods[k] || (basis[k] ?? "") !== (data.basis[k] ?? ""))
  const removed = run.data?.removed
  const removedTotal = removed ? Object.values(removed).reduce((a, b) => a + b, 0) : 0

  return (
    <div className="space-y-4">
      <p className="text-[12.5px] text-ink-500 max-w-3xl">
        How long Forge keeps each kind of audit and log record. Each night it removes what is older than its
        period, except for accounts under a legal hold. Removing details never breaks the audit chain: the record of
        who did what stays, and the chain still checks out.
      </p>

      {data.legal_review ? (
        <div className="rounded-md bg-green-100 text-ink-700 p-3 text-[13px]">
          <b>Confirmed by {data.legal_review.reviewed_by}</b>, recorded by <Actor actor={data.legal_review.recorded_by} /> on{" "}
          {formatDateTime(data.legal_review.recorded_at)}{data.legal_review.note ? ` (“${data.legal_review.note}”)` : ""}.
          Changing a period or its law clears this confirmation.
        </div>
      ) : (
        <div className="rounded-md bg-gold-100 text-ink-700 p-3 text-[13px]">
          <b>Not yet confirmed by a CA or lawyer.</b> These periods are {data.is_default ? "the" : "based on the"}{" "}
          {data.profiles[data.profile] ?? data.profile} profile: a suggested starting point, not legal advice. Have them
          checked before going live, then record who confirmed them below.
        </div>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>Periods</CardTitle>
          <Tag variant="teal">{data.profiles[data.profile] ?? data.profile}</Tag>
        </CardHeader>
        <CardContent className="space-y-3">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-line">
                <th className="p-2 font-semibold w-1/4">What</th>
                <th className="p-2 font-semibold w-44">Kept for</th>
                <th className="p-2 font-semibold">Law or reason</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k} className="border-b border-line last:border-0 align-top">
                  <td className="p-2 text-ink-900">{data.categories[k]}</td>
                  <td className="p-2">
                    {canManage ? (
                      <div className="space-y-1">
                        <div className="flex items-center gap-1.5">
                          <Input type="number" min={minimum(data, k)} max={600} className="w-20" aria-label={`${data.categories[k]}: months`}
                            value={periods[k] ?? ""} onChange={(e) => setPeriods({ ...periods, [k]: e.target.value })} />
                          <span className="text-ink-500">months</span>
                        </div>
                        <span className="block text-[11px] text-ink-300">
                          {monthsLabel(Number(periods[k]))}; at least {minimum(data, k)}
                        </span>
                      </div>
                    ) : <span>{data.periods[k]} months ({monthsLabel(data.periods[k])})</span>}
                  </td>
                  <td className="p-2">
                    {canManage ? (
                      <textarea rows={2} className={cn(inputClass, "text-[12.5px]")} aria-label={`${data.categories[k]}: law or reason`}
                        value={basis[k] ?? ""} onChange={(e) => setBasis({ ...basis, [k]: e.target.value })} />
                    ) : <span className="text-ink-700">{data.basis[k]}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {canManage && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" disabled={!changed || problems.length > 0 || save.isPending} onClick={() => save.mutate()}>
                {save.isPending ? "Saving…" : "Save periods"}
              </Button>
              {changed && (
                <Button size="sm" variant="secondary" onClick={() => {
                  setPeriods(Object.fromEntries(Object.entries(data.periods).map(([k, v]) => [k, String(v)])))
                  setBasis({ ...data.basis })
                }}>Undo changes</Button>
              )}
              {problems.length > 0 && <span className="text-[12px] text-red-500">{problems.join("; ")}</span>}
              {changed && problems.length === 0 && data.legal_review && (
                <span className="text-[12px] text-amber-500">Saving clears the legal confirmation.</span>
              )}
              <ErrorText error={save.error} fallback="Could not save the periods" />
            </div>
          )}
          <p className="text-[11.5px] text-ink-300">
            The audit skeleton is never kept under 8 years, and nothing else under {data.minimums.other} months, so a
            slip cannot wipe recent history. {data.updated_by && <>Last changed by <Actor actor={data.updated_by} />{data.updated_at ? ` on ${formatDateTime(data.updated_at)}` : ""}.</>}
          </p>
        </CardContent>
      </Card>

      {canManage && (
        <Card>
          <CardHeader><CardTitle>Record who confirmed these periods</CardTitle></CardHeader>
          <CardContent>
            <form className="grid gap-3 md:grid-cols-[1fr_2fr_auto] items-end" onSubmit={(e) => { e.preventDefault(); review.mutate() }}>
              <Field label="Confirmed by" hint="Name and firm of the CA or lawyer">
                <Input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="e.g. A. Rao, Rao & Co." />
              </Field>
              <Field label="Note (optional)">
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. opinion dated 9 Oct 2026, ref 42" />
              </Field>
              <Button size="sm" type="submit" disabled={reviewer.trim().length < 3 || changed || review.isPending}
                title={changed ? "Save or undo your changes first" : undefined}>
                Record confirmation
              </Button>
            </form>
            <ErrorText error={review.error} fallback="Could not record the confirmation" />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle>Legal holds</CardTitle></CardHeader>
        <CardContent>
          <p className="text-[12px] text-ink-500 mb-2">
            Nothing about an account on hold (or an account merged into it) is removed or erased until the hold is
            lifted. Place or lift a hold on the account's page.
          </p>
          {holds.isLoading ? <Loading /> : !holds.data || holds.data.length === 0 ? <Empty>No account is on hold.</Empty> : (
            <ul className="divide-y divide-line text-[12.5px]">
              {holds.data.map((h) => (
                <li key={h.id} className="py-2 flex flex-wrap items-center gap-2">
                  <Link to={`/accounts/${h.account_id}`} className="font-semibold text-teal-600 hover:underline">
                    {h.account_name ?? "An account"}
                  </Link>
                  <span className="text-ink-700">“{h.reason}”</span>
                  <span className="text-ink-300 flex items-center gap-1">placed by <Actor actor={h.placed_by} /> on {formatDateTime(h.placed_at)}</span>
                </li>
              ))}
            </ul>
          )}
          <ErrorText error={holds.error} fallback="Could not load the legal holds" />
        </CardContent>
      </Card>

      {canManage && (
        <Card>
          <CardHeader><CardTitle>Clean-up</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-[12.5px]">
            <p className="text-ink-500">
              Runs every night at 3:00 IST. Running it now does the same, for your company only, and is recorded in the
              audit log.
            </p>
            <Button size="sm" variant="secondary" disabled={run.isPending} onClick={() => run.mutate()}>
              {run.isPending ? "Running…" : "Run clean-up now"}
            </Button>
            {removed && (
              <p className="text-ink-700">
                {removedTotal === 0 ? "Nothing was old enough to remove." : `Removed ${Object.entries(removed)
                  .filter(([, n]) => n > 0).map(([k, n]) => `${n} ${REMOVED[k] ?? k}`).join(", ")}.`}
              </p>
            )}
            <ErrorText error={run.error} fallback="Could not run the clean-up" />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
