import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Field"
import { ErrorText } from "@/components/ui/States"
import { hasPermission } from "@/lib/permissions"
import { formatDateTime } from "@/lib/utils"
import { useAuthStore, usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import type { Stop, Takeover } from "./types"

/** On a record page: whether a person has taken it over from agents (T8), and
 *  whether agents are stopped on it; with the buttons to change that. */
export function TakeoverPanel({ recordType, recordId }: { recordType: "account" | "contact"; recordId: string }) {
  const perms = usePermissions()
  const me = useAuthStore((s) => s.me)
  const canTake = hasPermission(perms, "records.takeover")
  const canSeeStops = hasPermission(perms, "agents.read")
  const qc = useQueryClient()
  const [reason, setReason] = useState("")
  const [asking, setAsking] = useState(false)
  const key = ["takeovers", recordType, recordId]
  const { data } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<Takeover[]>("/takeovers", { params: { record_type: recordType, record_id: recordId } })).data,
  })
  const stops = useQuery({
    queryKey: ["stops", false],
    queryFn: async () => (await api.get<Stop[]>("/kill-switches")).data,
    enabled: canSeeStops && recordType === "account",
  })
  const take = useMutation({
    mutationFn: async () => api.post("/takeovers", { record_type: recordType, record_id: recordId, reason: reason || null }),
    onSuccess: () => { setAsking(false); setReason(""); qc.invalidateQueries({ queryKey: key }) },
  })
  const handBack = useMutation({
    mutationFn: async (id: string) => api.post(`/takeovers/${id}/hand-back`),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  })
  const active = data?.[0]
  const stopped = stops.data?.filter((s) => s.scope === "tenant" || s.account_id === recordId) ?? []
  const mine = active && (active.taken_by === `user:${me?.id}` || hasPermission(perms, "agents.manage"))

  return (
    <div className="space-y-2">
      {stopped.map((s) => (
        <div key={s.id} className="rounded-md bg-red-100 text-[13px] p-3 text-ink-700">
          <b className="text-red-500">Agents are stopped {s.scope === "tenant" ? "everywhere" : "on this account"}.</b> {s.reason}
        </div>
      ))}
      {active ? (
        <div className="rounded-md bg-gold-100 text-ink-700 text-[13px] p-3 flex items-center justify-between gap-3">
          <span>
            <b>Taken over by <Actor actor={active.taken_by} /></b> since {formatDateTime(active.started_at)}
            {active.reason && <> ({active.reason})</>}. Agents will not act on this {recordType} until it is handed back.
          </span>
          {canTake && mine && (
            <Button size="sm" variant="secondary" onClick={() => handBack.mutate(active.id)} disabled={handBack.isPending}>Hand back to agents</Button>
          )}
        </div>
      ) : canTake && (
        asking ? (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); take.mutate() }}>
            <Input autoFocus placeholder="Why (optional), e.g. sensitive negotiation" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button type="submit" variant="gold" size="sm" disabled={take.isPending}>Take over</Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setAsking(false)}>Cancel</Button>
          </form>
        ) : (
          <button onClick={() => setAsking(true)} className="text-[12.5px] text-teal-600 hover:underline">
            Take over from agents
          </button>
        )
      )}
      <ErrorText error={take.error ?? handBack.error} fallback="Could not change the takeover" />
    </div>
  )
}
