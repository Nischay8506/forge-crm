import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent } from "@/components/ui/Card"
import { inputClass } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useChannels } from "./hooks"
import { PURPOSE_LABELS, STATUS_TONE, partyLabel } from "./labels"
import { CustomerView } from "./MessageDetailPage"
import type { MessageView } from "./types"

const ACTABLE = ["sent", "delivered", "read", "bounced"]

function Mail({ m, canAct }: { m: MessageView; canAct: boolean }) {
  const qc = useQueryClient()
  const [replying, setReplying] = useState(false)
  const [text, setText] = useState("")
  const [note, setNote] = useState<string | null>(null)
  const refresh = () => { qc.invalidateQueries({ queryKey: ["messages"] }); qc.invalidateQueries({ queryKey: ["message"] }) }
  const act = useMutation({
    mutationFn: async (kind: "reply" | "bounce" | "unsubscribe") => (await api.post<MessageView & { unsubscribed?: boolean; already?: boolean }>(
      `/channels/test-inbox/${m.id}/${kind}`, kind === "reply" ? { body: text } : undefined)).data,
    onSuccess: (d, kind) => {
      if (kind === "reply") { setReplying(false); setText(""); setNote("Their reply arrived in Forge as a received message.") }
      if (kind === "bounce") setNote("The email bounced: Forge marked it and will not retry it.")
      if (kind === "unsubscribe") setNote(d.already ? "They had already stopped promotional messages." : "They stopped promotional messages on every channel.")
      refresh()
    },
  })
  const incoming = m.direction === "in"
  return (
    <li className={cn("p-3 space-y-2", incoming && "bg-teal-100/40")}>
      <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
        <Tag variant={incoming ? "teal" : "neutral"}>{incoming ? "Customer to us" : "Us to customer"}</Tag>
        <Tag variant={STATUS_TONE[m.status] ?? "neutral"}>{m.status_label}</Tag>
        {m.purpose && !incoming && <Tag>{PURPOSE_LABELS[m.purpose] ?? m.purpose}</Tag>}
        <span className="text-ink-700">{partyLabel(m)}</span>
        <span className="text-ink-300">{incoming ? m.from_address : m.to_address}</span>
        <span className="flex-1" />
        <span className="text-ink-300">{formatDateTime(m.sent_at ?? m.created_at)}</span>
      </div>
      <Link to={`/messages/${m.id}`} className="block font-semibold text-[13.5px] text-ink-900 hover:underline">{m.subject || "(no subject)"}</Link>
      <CustomerView m={m} />
      {canAct && !incoming && ACTABLE.includes(m.status) && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {!replying && <Button size="sm" variant="secondary" onClick={() => { setReplying(true); setNote(null) }}>Reply as the customer</Button>}
            {m.status !== "bounced" && <Button size="sm" variant="secondary" disabled={act.isPending} onClick={() => act.mutate("bounce")}>Make it bounce</Button>}
            {m.purpose === "promotional" && (
              <Button size="sm" variant="secondary" disabled={act.isPending} onClick={() => act.mutate("unsubscribe")}>Press unsubscribe</Button>
            )}
          </div>
          {replying && (
            <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); act.mutate("reply") }}>
              <textarea autoFocus rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label="The customer's reply"
                placeholder="Write as the customer. Try another language, or just STOP." className={inputClass} />
              <div className="flex gap-2">
                <Button size="sm" type="submit" disabled={!text.trim() || act.isPending}>Send their reply</Button>
                <Button size="sm" type="button" variant="secondary" onClick={() => setReplying(false)}>Back</Button>
              </div>
            </form>
          )}
        </div>
      )}
      {note && <p className="text-[12.5px] text-green-500">{note}</p>}
      <ErrorText error={act.error} fallback="Could not do that" />
    </li>
  )
}

/** What the simulator "sent" (L11 A), and the customer's side of it. */
export function TestInboxPage() {
  const canAct = hasPermission(usePermissions(), "messages.send")
  const channels = useChannels()
  const sim = channels.data?.simulator_addresses
  const { data, isLoading, error } = useQuery({
    queryKey: ["messages", "test-inbox"],
    queryFn: async () => (await api.get<MessageView[]>("/messages", { params: { test_inbox: true, limit: 100 } })).data,
    refetchInterval: 10_000,
  })
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-ink-500 max-w-3xl">
        Emails Forge sent while email is in test mode land here instead of a real mailbox. Act as the customer: reply (a reply of
        just “STOP” stops their promotional messages), make an email bounce, or press its unsubscribe link.
        {sim && <> To try failures, give a test contact the address <code>{sim.bounce}</code> (always bounces), <code>{sim.outage}</code> (mail
          server down, retried) or <code>{sim.lost}</code> (connection drops, marked as unknown).</>}
      </p>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the Test inbox" />
      {data && (
        <Card>
          <CardContent className="p-0">
            {data.length === 0 ? <Empty>Nothing yet. Send a message to a contact and it lands here.</Empty> : (
              <ul className="divide-y divide-line">{data.map((m) => <Mail key={m.id} m={m} canAct={canAct} />)}</ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
