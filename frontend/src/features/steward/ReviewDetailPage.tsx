import { useEffect, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { ScoreBadge } from "@/components/ui/ScoreBadge"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDate } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { BAND_LABELS, SOURCE_LABELS, effectTone, fieldLabel } from "./labels"
import { BRANCH_LOCKED, branchRule, defaultPicks, differingFields, fieldChoices } from "./mergeChoices"
import type { CandidateDetail } from "./types"

const show = (v: unknown) => (v === null || v === undefined || v === "" ? "–" : String(v))

/** Side-by-side comparison: the person picks the record that survives and, for each
 *  field that differs, which value to keep. Gold = a human decides. */
export function ReviewDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const canReview = hasPermission(usePermissions(), "steward.review")
  const { data, isLoading, error } = useQuery({
    queryKey: ["candidate", id],
    queryFn: async () => (await api.get<CandidateDetail>(`/steward/candidates/${id}`)).data,
  })
  const [survivor, setSurvivor] = useState<string>("")
  const [picks, setPicks] = useState<Record<string, string>>({})
  useEffect(() => {
    if (!data) return
    setSurvivor(data.suggested_survivor_id)
    setPicks(defaultPicks(data))
  }, [data])

  const done = () => {
    qc.invalidateQueries({ queryKey: ["candidates"] })
    qc.invalidateQueries({ queryKey: ["merges"] })
    qc.invalidateQueries({ queryKey: ["account"] })
    navigate("/steward/review")
  }
  const merge = useMutation({
    mutationFn: async () => api.post(`/steward/candidates/${id}/merge`, {
      survivor_id: survivor, field_choices: fieldChoices(data!, survivor, picks) }),
    onSuccess: done,
  })
  const notDuplicate = useMutation({
    mutationFn: async () => api.post(`/steward/candidates/${id}/not-duplicate`),
    onSuccess: done,
  })

  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load this pair" />
  const c = data.candidate
  const ids = [c.left_id, c.right_id]
  const diff = new Set(differingFields(data))
  const branch = branchRule(data)
  const decided = c.status !== "open"
  const recordLink = (rid: string) => c.record_type === "account" ? `/accounts/${rid}` : null

  return (
    <div className="space-y-4">
      <Link to="/steward/review" className="text-[12.5px] text-teal-600 hover:underline">← Review queue</Link>
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>Are these the same {c.record_type}?</CardTitle>
          <div className="flex items-center gap-2">
            <Tag variant="gold">{BAND_LABELS[c.band]}</Tag>
            <span className="font-mono text-[12px] text-ink-500">score {Math.round(c.score)}</span>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {c.reasons.map((r) => (
              <Tag key={r.signal} variant={effectTone(r.effect)} className="normal-case tracking-normal text-[11px]">{r.detail}</Tag>
            ))}
          </div>
          {branch && (
            <p className="text-[12.5px] rounded-md bg-teal-100 text-teal-600 p-2.5">
              {data.suggested_choices._branch_site.reason}. GSTIN and address stay with the surviving record.
            </p>
          )}
          {decided && <p className="text-[12.5px] text-amber-500">This pair was already decided ({c.status.replace("_", " ")}).</p>}
        </CardContent>
      </Card>

      <Card>
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-line">
              <th className="p-3 w-[160px]" />
              {ids.map((rid) => (
                <th key={rid} className="p-3 text-left align-top">
                  <label className={cn("flex items-start gap-2 rounded-md border-[1.5px] p-2.5 cursor-pointer",
                    survivor === rid ? "border-gold-500 bg-gold-100" : "border-line")}>
                    <input type="radio" name="survivor" checked={survivor === rid} disabled={decided}
                      onChange={() => setSurvivor(rid)} className="mt-1 accent-[#c9a227]" />
                    <span className="space-y-0.5">
                      <span className="block font-semibold text-ink-900">Keep this record</span>
                      <span className="block font-normal text-[12px] text-ink-500">
                        {SOURCE_LABELS[String(data.records[rid].source)] ?? data.records[rid].source} · created {formatDate(String(data.records[rid].created_at))}
                        {rid === data.suggested_survivor_id && " · suggested"}
                      </span>
                      <span className="block"><ScoreBadge score={data.records[rid].hygiene_score as number | null} /></span>
                      {recordLink(rid) && <Link to={recordLink(rid)!} className="block font-normal text-[12px] text-teal-600 hover:underline">Open record</Link>}
                    </span>
                  </label>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.fields.filter((f) => ids.some((rid) => show(data.records[rid][f]) !== "–")).map((f) => {
              const differs = diff.has(f)
              const locked = branch && BRANCH_LOCKED.includes(f)
              return (
                <tr key={f} className={cn("border-b border-line last:border-0", differs && "bg-surface-0")}>
                  <td className="p-3 text-ink-500">{fieldLabel(f)}</td>
                  {ids.map((rid) => {
                    const v = data.records[rid][f]
                    const chosen = locked ? rid === survivor : picks[f] === rid
                    return (
                      <td key={rid} className="p-3">
                        {differs && !decided ? (
                          <label className={cn("flex items-center gap-2", (locked || v === null || v === "") && "opacity-70")}>
                            <input type="radio" name={`f-${f}`} checked={chosen} disabled={locked}
                              onChange={() => setPicks({ ...picks, [f]: rid })} className="accent-[#c9a227]" />
                            <span className={cn(chosen && "font-semibold")}>{show(v)}</span>
                          </label>
                        ) : <span className={cn(!differs && "text-ink-500")}>{show(v)}</span>}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>

      {canReview && !decided && (
        <div className="flex items-center gap-2">
          <Button variant="gold" disabled={merge.isPending || !survivor} onClick={() => merge.mutate()}>
            {merge.isPending ? "Merging…" : "Merge into the kept record"}
          </Button>
          <Button variant="secondary" disabled={notDuplicate.isPending} onClick={() => notDuplicate.mutate()}>
            Not the same
          </Button>
          <span className="text-[12px] text-ink-500 ml-2">
            The selected values are kept. The other record stays as merged and the merge can be
            undone from Merge history.
          </span>
        </div>
      )}
      <ErrorText error={merge.error ?? notDuplicate.error} fallback="Could not save the decision" />
    </div>
  )
}
