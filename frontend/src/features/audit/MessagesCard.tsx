import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Select } from "@/components/ui/Field"
import { Empty, ErrorText } from "@/components/ui/States"
import { hasPermission } from "@/lib/permissions"
import { usePermissions } from "@/stores/authStore"
import { MessageRow } from "@/features/messages/MessageRow"
import type { MessageView } from "@/features/messages/types"

/** Messages to and from this account's contacts (Step 6), plus the Sandbox Agent's
 *  test drafts from Step 4 (R7), which are never sent. */
export function MessagesCard({ accountId, contacts = [] }: {
  accountId: string; contacts?: { id: string; full_name: string }[]
}) {
  const perms = usePermissions()
  const canSend = hasPermission(perms, "messages.send")
  const canDraft = hasPermission(perms, "agents.manage")
  const qc = useQueryClient()
  const [to, setTo] = useState("")
  const { data } = useQuery({
    queryKey: ["messages", "account", accountId],
    queryFn: async () => (await api.get<MessageView[]>("/messages", { params: { account_id: accountId, limit: 20 } })).data,
    refetchInterval: 15_000,
  })
  const draft = useMutation({
    mutationFn: async () => (await api.post<{ status: string; reasons: string[] }>(
      "/agents/sandbox/draft-message", { account_id: accountId, contact_id: to || null })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["messages"] }),
  })
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <CardTitle>Messages</CardTitle>
        <div className="flex flex-wrap items-center gap-1.5">
          {(canSend || canDraft) && contacts.length > 0 && (
            <Select className="h-8 py-1 w-40 text-[12.5px]" aria-label="Message to" value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">Choose a contact</option>
              {contacts.map((c) => <option key={c.id} value={c.id}>To {c.full_name}</option>)}
            </Select>
          )}
          {canSend && (
            <Link to={to ? `/messages/new?contact=${to}` : "/messages/new"}>
              <Button size="sm">Write a message</Button>
            </Link>
          )}
          {canDraft && (
            <Button size="sm" variant="ghost" disabled={draft.isPending} onClick={() => draft.mutate()}
              title="The Sandbox Agent drafts a test message. It is never sent.">
              Test draft
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {draft.data && draft.data.status !== "done" && (
          <p className="text-[12.5px] text-amber-500">
            The Sandbox Agent did not draft it ({draft.data.status}): {draft.data.reasons.join("; ")}
          </p>
        )}
        <ErrorText error={draft.error} fallback="Could not draft a test message" />
        {!data || data.length === 0 ? <Empty>No messages yet.</Empty> : (
          <div className="divide-y divide-line -mx-3">
            {data.map((m) => <MessageRow key={m.id} m={m} showChannel />)}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
