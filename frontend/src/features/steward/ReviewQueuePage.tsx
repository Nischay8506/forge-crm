import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { effectTone } from "./labels"
import type { CandidateRow, RecordType } from "./types"

const name = (r: CandidateRow["left"]) => String(r.name ?? r.full_name ?? "(removed)")
const sub = (r: CandidateRow["left"]) => [r.city, r.gstin, r.email].filter(Boolean).join(" · ")

export function ReviewQueuePage() {
  const canReview = hasPermission(usePermissions(), "steward.review")
  const qc = useQueryClient()
  const [type, setType] = useState<RecordType>("account")
  const { data, isLoading, error } = useQuery({
    queryKey: ["candidates", type],
    queryFn: async () => (await api.get<CandidateRow[]>("/steward/candidates", {
      params: { record_type: type, limit: 200 } })).data,
  })
  const rescan = useMutation({
    mutationFn: async () => (await api.post<{ checked: number; auto_merged: number; queued: number }>(
      "/steward/rescan", null, { params: { record_type: type } })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["candidates"] }),
  })

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex gap-1 rounded-md bg-surface-2 p-1">
          {(["account", "contact"] as const).map((t) => (
            <button key={t} onClick={() => setType(t)}
              className={cn("px-3 py-1 text-[12.5px] rounded font-medium text-ink-500",
                type === t && "bg-surface-1 text-ink-900 shadow-sm")}>
              {t === "account" ? "Accounts" : "Contacts"}
            </button>
          ))}
        </div>
        {canReview && (
          <div className="flex items-center gap-3">
            {rescan.data && (
              <span className="text-[12px] text-ink-500">
                Checked {rescan.data.checked}: {rescan.data.auto_merged} merged, {rescan.data.queued} queued
              </span>
            )}
            <Button size="sm" variant="secondary" disabled={rescan.isPending} onClick={() => rescan.mutate()}>
              {rescan.isPending ? "Checking…" : "Check all records again"}
            </Button>
          </div>
        )}
      </div>
      <p className="text-[12.5px] text-ink-500">
        Pairs the Data Steward thinks may be the same {type}, but is not sure enough to merge on its own.
        Merges it did by itself are in Merge history.
      </p>
      {isLoading && <Loading />}
      <ErrorText error={error ?? rescan.error} fallback="Could not load the review queue" />
      {data && (
        <Card>
          {data.length === 0 ? <Empty>Nothing to review.</Empty> : (
            <ul className="divide-y divide-line">
              {data.map((c) => (
                <li key={c.id}>
                  <Link to={`/steward/review/${c.id}`} className="flex items-center gap-4 p-3 hover:bg-surface-0">
                    <div className="flex-1 grid grid-cols-2 gap-4 text-[13px]">
                      <div><div className="font-medium text-ink-900">{name(c.left)}</div><div className="text-ink-300 text-[12px]">{sub(c.left)}</div></div>
                      <div><div className="font-medium text-ink-900">{name(c.right)}</div><div className="text-ink-300 text-[12px]">{sub(c.right)}</div></div>
                    </div>
                    <div className="flex flex-wrap gap-1 justify-end max-w-[340px]">
                      {c.reasons.filter((r) => r.effect !== "info").slice(0, 3).map((r) => (
                        <Tag key={r.signal} variant={effectTone(r.effect)} title={r.detail}>{r.signal.replace(/_/g, " ")}</Tag>
                      ))}
                    </div>
                    <span className="font-mono text-[12px] text-ink-500 w-10 text-right">{Math.round(c.score)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  )
}
