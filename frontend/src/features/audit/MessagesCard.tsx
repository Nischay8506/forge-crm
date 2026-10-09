import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Select } from "@/components/ui/Field"
import { Empty, ErrorText } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import type { Message } from "@/features/agents/types"

const CHANNEL: Record<string, string> = { email: "Email", whatsapp: "WhatsApp" }

/** Messages to this account (R7). Until Step 6 only test drafts exist; none is sent. */
export function MessagesCard({ accountId, contacts = [] }: {
  accountId: string; contacts?: { id: string; full_name: string }[]
}) {
  const perms = usePermissions()
  const canTrace = hasPermission(perms, "audit.read")
  const canDraft = hasPermission(perms, "agents.manage")
  const qc = useQueryClient()
  const [to, setTo] = useState("")
  const { data } = useQuery({
    queryKey: ["messages", accountId],
    queryFn: async () => (await api.get<Message[]>("/messages", { params: { account_id: accountId } })).data,
  })
  const draft = useMutation({
    mutationFn: async () => (await api.post<{ status: string; reasons: string[] }>(
      "/agents/sandbox/draft-message", { account_id: accountId, contact_id: to || null })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["messages", accountId] }),
  })
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>Messages</CardTitle>
        {canDraft && (
          <div className="flex items-center gap-1.5">
            {contacts.length > 0 && (
              <Select className="h-8 py-1 w-40 text-[12.5px]" aria-label="Test message to" value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">To the account</option>
                {contacts.map((c) => <option key={c.id} value={c.id}>To {c.full_name}</option>)}
              </Select>
            )}
            <Button size="sm" variant="ghost" disabled={draft.isPending} onClick={() => draft.mutate()}
              title="The Sandbox Agent drafts a test message. It is never sent.">
              Draft a test message
            </Button>
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        {draft.data && draft.data.status !== "done" && (
          <p className="text-[12.5px] text-amber-500">
            The Sandbox Agent did not draft it ({draft.data.status}): {draft.data.reasons.join("; ")}
          </p>
        )}
        <ErrorText error={draft.error} fallback="Could not draft a test message" />
        {!data || data.length === 0 ? <Empty>No messages yet. Email and WhatsApp arrive in Step 6.</Empty> : (
          <ul className="divide-y divide-line">
            {data.map((m) => (
              <li key={m.id} className="py-2 space-y-1 text-[12.5px]">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Tag>{CHANNEL[m.channel] ?? m.channel}</Tag>
                  <Tag variant={m.status === "sent" ? "green" : m.status === "failed" ? "red" : "neutral"}>
                    {m.status === "draft" ? "Draft, not sent" : m.status}
                  </Tag>
                  {m.ai_generated && <Tag variant="teal">{m.disclosure ?? "Written by AI"}</Tag>}
                  <span className="text-ink-300">{formatDateTime(m.created_at)}</span>
                  {m.contact_name && <span className="text-ink-500">to {m.contact_name}</span>}
                </div>
                {m.subject && <p className="font-semibold text-ink-900">{m.subject}</p>}
                <p className="text-ink-700 line-clamp-2">{m.body}</p>
                {canTrace && (
                  <Link to={`/agents/messages/${m.id}`} className="text-teal-600 hover:underline">
                    Trace this message
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
