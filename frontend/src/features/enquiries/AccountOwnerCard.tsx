import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Select } from "@/components/ui/Field"
import { usePeople } from "./hooks"

/** Who owns the account: its enquiries go to them first (N12 A). Sites without an
 *  owner use their parent's. */
export function AccountOwnerCard({ accountId, ownerId, canChange }: { accountId: string; ownerId: string | null; canChange: boolean }) {
  const qc = useQueryClient()
  const people = usePeople()
  const [value, setValue] = useState(ownerId ?? "")
  const save = useMutation({
    mutationFn: async (user_id: string) => api.put(`/enquiries/setup/account-owner/${accountId}`, { user_id: user_id || null }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["account", accountId] }),
  })
  const name = people.data?.find((p) => p.id === ownerId)?.full_name
  return (
    <Card>
      <CardHeader><CardTitle>Account owner</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-[13px]">
        {canChange ? (
          <Select value={value} aria-label="Account owner" disabled={save.isPending}
            onChange={(e) => { setValue(e.target.value); save.mutate(e.target.value) }}>
            <option value="">No owner (the parent's, or the routing rules)</option>
            {people.data?.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
          </Select>
        ) : <p className="text-ink-900">{name ?? "No owner"}</p>}
        <p className="text-[11.5px] text-ink-300">New enquiries from this account go to the owner first; while they are away, to their stand-in.</p>
        {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not change the owner")}</p>}
        {save.isSuccess && <p role="status" className="text-green-500 text-[12px]">Saved.</p>}
      </CardContent>
    </Card>
  )
}
