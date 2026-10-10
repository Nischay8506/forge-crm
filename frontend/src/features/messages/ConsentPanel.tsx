import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Input, Select } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDate, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { CHANNEL_LABELS } from "./labels"
import type { ConsentView } from "./types"

/** Only these can be recorded by a person; the link and STOP are recorded by Forge. */
const PERSON_HOWS = ["form", "in_person", "phone", "email", "import", "person"]

/** A contact's consent to promotional messages, per channel, with its history (L5 A). */
export function ConsentPanel({ contactId, onClose }: { contactId: string; onClose: () => void }) {
  const canWrite = hasPermission(usePermissions(), "accounts.write")
  const qc = useQueryClient()
  const [form, setForm] = useState({ channel: "all", status: "withdrawn", how: "phone", note: "" })
  const { data, isLoading, error } = useQuery({
    queryKey: ["consent", contactId],
    queryFn: async () => (await api.get<ConsentView>(`/contacts/${contactId}/consent`)).data,
  })
  const record = useMutation({
    mutationFn: async () => (await api.post(`/contacts/${contactId}/consent`, { ...form, note: form.note.trim() || null })).data,
    onSuccess: () => { setForm((f) => ({ ...f, note: "" })); qc.invalidateQueries({ queryKey: ["consent", contactId] }) },
  })
  return (
    <div className="rounded-md bg-surface-2 p-2.5 space-y-2.5 text-[12.5px]">
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load consent" />
      {data && (
        <>
          <div className="flex flex-wrap gap-3">
            {data.channels.map((c) => (
              <div key={c.channel} className="flex items-center gap-1.5">
                <b className="text-ink-700">{CHANNEL_LABELS[c.channel] ?? c.channel}:</b>
                <Tag variant={c.may_promote ? (c.status === "granted" ? "green" : "neutral") : "red"}>
                  {c.status === "withdrawn" ? "Stopped" : c.status === "granted" ? "Agreed" : "Not asked"}
                </Tag>
                {c.how_label && <span className="text-ink-300">{c.how_label}{c.at ? `, ${formatDate(c.at)}` : ""}</span>}
              </div>
            ))}
          </div>
          <p className="text-ink-500">
            Email is opt-out: promotional emails may go until the customer asks to stop. Messages about their business with us always go.
          </p>
          {data.history.length > 0 && (
            <details>
              <summary className="cursor-pointer text-ink-500">History ({data.history.length})</summary>
              <ul className="mt-1 space-y-0.5">
                {data.history.map((h) => (
                  <li key={h.id} className="text-ink-700">
                    {formatDateTime(h.recorded_at)}: {h.status === "withdrawn" ? "stopped" : "agreed to"} {CHANNEL_LABELS[h.channel] ?? h.channel} ·{" "}
                    {h.how_label} · <Actor actor={h.recorded_by} />{h.note ? ` · “${h.note}”` : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {canWrite && (
            <form className="flex flex-wrap items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); record.mutate() }}>
              <span className="text-ink-700">The customer</span>
              <Select className="w-auto h-8 py-1 text-[12.5px]" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} aria-label="What they said">
                <option value="withdrawn">asked to stop promotional messages</option>
                <option value="granted">agreed to promotional messages</option>
              </Select>
              <span className="text-ink-700">on</span>
              <Select className="w-auto h-8 py-1 text-[12.5px]" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })} aria-label="Channel">
                {["all", "email", "whatsapp", "sms"].map((c) => <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>)}
              </Select>
              <Select className="w-auto h-8 py-1 text-[12.5px]" value={form.how} onChange={(e) => setForm({ ...form, how: e.target.value })} aria-label="How">
                {PERSON_HOWS.map((h) => <option key={h} value={h}>{data.hows[h] ?? h}</option>)}
              </Select>
              <Input className="h-8 py-1 text-[12.5px] w-48" placeholder="Note (optional)" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
              <Button size="sm" type="submit" disabled={record.isPending}>Record</Button>
            </form>
          )}
          <ErrorText error={record.error} fallback="Could not record it" />
        </>
      )}
      <button type="button" className="text-ink-500 hover:underline" onClick={onClose}>Close</button>
    </div>
  )
}
