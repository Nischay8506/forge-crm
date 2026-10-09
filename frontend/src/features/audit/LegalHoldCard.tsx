import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Input } from "@/components/ui/Field"
import { ErrorText } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import type { LegalHold } from "@/features/agents/types"

/** A legal hold stops retention and erasure from removing anything about this
 * account (R5) until someone lifts it. */
export function LegalHoldCard({ accountId }: { accountId: string }) {
  const perms = usePermissions()
  const canManage = hasPermission(perms, "audit.manage")
  const qc = useQueryClient()
  const [reason, setReason] = useState("")
  const [open, setOpen] = useState(false)
  const key = ["legal-holds", accountId]
  const { data } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<LegalHold[]>("/audit/legal-holds", { params: { account_id: accountId } })).data,
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ["legal-holds"] })
  const place = useMutation({
    mutationFn: async () => api.post("/audit/legal-holds", { account_id: accountId, reason }),
    onSuccess: () => { setReason(""); setOpen(false); refresh() },
  })
  const lift = useMutation({
    mutationFn: async (id: string) => api.post(`/audit/legal-holds/${id}/lift`, {}),
    onSuccess: refresh,
  })
  const hold = data?.[0]
  if (!hold && !canManage) return null
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>Legal hold</CardTitle>
        {hold && <Tag variant="red">On hold</Tag>}
      </CardHeader>
      <CardContent className="space-y-2 text-[12.5px]">
        {hold ? (
          <>
            <p className="text-ink-700">“{hold.reason}”</p>
            <p className="text-ink-300 flex flex-wrap items-center gap-1">
              Placed by <Actor actor={hold.placed_by} /> on {formatDateTime(hold.placed_at)}
            </p>
            <p className="text-ink-500">Nothing about this account is deleted by the retention rules or erased while the hold is on.</p>
            {canManage && (
              <Button size="sm" variant="secondary" disabled={lift.isPending} onClick={() => lift.mutate(hold.id)}>
                Lift the hold
              </Button>
            )}
          </>
        ) : open ? (
          <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); place.mutate() }}>
            <Input autoFocus placeholder="Why? e.g. payment dispute, notice received" value={reason}
              onChange={(e) => setReason(e.target.value)} />
            <div className="flex gap-2">
              <Button size="sm" type="submit" disabled={reason.trim().length < 5 || place.isPending}>Place hold</Button>
              <Button size="sm" type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
            </div>
          </form>
        ) : (
          <>
            <p className="text-ink-500">Keeps everything about this account, for example during a dispute or an audit.</p>
            <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>Place a legal hold</Button>
          </>
        )}
        <ErrorText error={place.error ?? lift.error} fallback="Could not change the hold" />
      </CardContent>
    </Card>
  )
}
