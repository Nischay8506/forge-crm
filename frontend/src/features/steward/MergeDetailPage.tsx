import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "./Actor"
import { fieldLabel } from "./labels"
import type { MergeDetail } from "./types"

const show = (v: unknown) => (v === null || v === undefined || v === "" ? "–" : String(v))
const TABLE_NAMES: Record<string, string> = { contacts: "contact(s)", accounts: "site(s) or companies" }

export function MergeDetailPage() {
  const { id } = useParams()
  const qc = useQueryClient()
  const canUndo = hasPermission(usePermissions(), "steward.undo")
  const [confirming, setConfirming] = useState(false)
  const { data, isLoading, error } = useQuery({
    queryKey: ["merge", id],
    queryFn: async () => (await api.get<MergeDetail>(`/steward/merges/${id}`)).data,
  })
  const undo = useMutation({
    mutationFn: async () => api.post(`/steward/merges/${id}/undo`),
    onSuccess: () => {
      setConfirming(false)
      qc.invalidateQueries({ queryKey: ["merge", id] })
      qc.invalidateQueries({ queryKey: ["merges"] })
      qc.invalidateQueries({ queryKey: ["account"] })
    },
  })
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load this merge" />
  const isAccount = data.record_type === "account"
  const choices = Object.entries(data.field_choices).filter(([f]) => !f.startsWith("_"))
  const branch = data.field_choices._branch_site_id

  return (
    <div className="space-y-4">
      <Link to="/steward/merges" className="text-[12.5px] text-teal-600 hover:underline">← Merge history</Link>
      <Card>
        <CardHeader><CardTitle>
          {show(data.merged_name)} merged into {isAccount
            ? <Link to={`/accounts/${data.survivor_id}`} className="text-teal-600 hover:underline">{show(data.survivor_name)}</Link>
            : show(data.survivor_name)}
        </CardTitle></CardHeader>
        <CardContent className="space-y-2 text-[13px]">
          <div className="flex items-center gap-2 text-ink-500">
            <Actor actor={data.decided_by} /> · {formatDateTime(data.created_at)}
            {data.automatic && <span>· automatic (strong identifier match)</span>}
          </div>
          {Object.entries(data.repointed).map(([key, rows]) => (
            <p key={key} className="text-ink-500">{rows.length} {TABLE_NAMES[key.split(".")[0]] ?? key} moved to the kept record.</p>
          ))}
          {branch?.site_id && (
            <p className="text-ink-500">
              {branch.reason}: <Link to={`/accounts/${branch.site_id}`} className="text-teal-600 hover:underline">open branch site</Link>
            </p>
          )}
          {isAccount && <p><Link to={`/accounts/${data.merged_id}`} className="text-teal-600 hover:underline">Open the merged record</Link></p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Fields that differed</CardTitle></CardHeader>
        <CardContent>
          {choices.length === 0 ? <p className="text-ink-300 text-[13px]">The records had no conflicting values.</p> : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-ink-500 border-b border-line">
                  <th className="py-2 pr-3 font-semibold">Field</th>
                  <th className="py-2 pr-3 font-semibold">Kept record had</th>
                  <th className="py-2 pr-3 font-semibold">Merged record had</th>
                  <th className="py-2 font-semibold">Kept value and why</th>
                </tr>
              </thead>
              <tbody>
                {choices.map(([f, ch]) => (
                  <tr key={f} className="border-b border-line last:border-0">
                    <td className="py-2 pr-3 text-ink-500">{fieldLabel(f)}</td>
                    <td className={ch.take === "survivor" ? "py-2 pr-3 font-semibold" : "py-2 pr-3"}>{show(ch.survivor_value)}</td>
                    <td className={ch.take === "merged" ? "py-2 pr-3 font-semibold" : "py-2 pr-3"}>{show(ch.merged_value)}</td>
                    <td className="py-2 text-ink-500">{ch.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      {data.undone_at ? (
        <Card>
          <CardContent className="pt-4 space-y-1 text-[13px]">
            <div className="flex items-center gap-2"><Tag variant="amber">Undone</Tag>
              <span className="text-ink-500"><Actor actor={data.undone_by} /> · {formatDateTime(data.undone_at)}</span></div>
            {data.undo_notes?.map((n) => <p key={n} className="text-ink-500">{n}</p>)}
          </CardContent>
        </Card>
      ) : canUndo && (
        <div className="flex items-center gap-2">
          {!confirming ? (
            <Button variant="danger" onClick={() => setConfirming(true)}>Undo this merge</Button>
          ) : (
            <>
              <span className="text-[13px] text-ink-700">Both records become separate again and moved {isAccount ? "contacts and sites" : "links"} go back. Undo?</span>
              <Button variant="gold" disabled={undo.isPending} onClick={() => undo.mutate()}>Yes, undo</Button>
              <Button variant="secondary" onClick={() => setConfirming(false)}>Cancel</Button>
            </>
          )}
        </div>
      )}
      <ErrorText error={undo.error} fallback="Could not undo the merge" />
    </div>
  )
}
