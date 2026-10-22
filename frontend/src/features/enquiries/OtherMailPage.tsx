import { useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Input } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { useDebounced } from "@/lib/useDebounced"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { downloadFile } from "./files"
import { MAIL_LABELS, OTHER_LABELS } from "./labels"
import type { InboundEmailView } from "./types"

const SHOWS = [
  { key: "other", label: "Other mail", hint: "Read as not an enquiry" },
  { key: "review", label: "Review mail", hint: "Uncertain classification or incomplete AI extraction" },
  { key: "automatic", label: "Automatic", hint: "Out-of-office replies, bounces and our own mail" },
  { key: "failed", label: "Could not be read", hint: "Tried three times" },
  { key: "waiting", label: "Waiting to be read", hint: "Arrived, not read yet" },
]

/** Mail that is not an enquiry (N5 A): kept, labelled, and one click from becoming one. */
export function OtherMailPage() {
  const [search, setSearch] = useSearchParams()
  const show = SHOWS.find((s) => s.key === search.get("show"))?.key ?? "other"
  const label = search.get("label") ?? ""
  const [q, setQ] = useState("")
  const dq = useDebounced(q)
  const [openId, setOpenId] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ["enquiries", "mail", show, label, dq],
    queryFn: async () => (await api.get<InboundEmailView[]>("/enquiries/mail/other", { params: { show, label: label || undefined, q: dq || undefined } })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  })
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(search)
    if (v) next.set(k, v)
    else next.delete(k)
    if (k === "show") next.delete("label")
    setSearch(next, { replace: true })
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {SHOWS.map((s) => (
          <button key={s.key} type="button" title={s.hint} onClick={() => set("show", s.key === "other" ? null : s.key)}
            className={cn("rounded-full border px-3 py-1 text-[12.5px]",
              s.key === show ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500 hover:text-ink-900")}>
            {s.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {show === "other" && (
          <select value={label} onChange={(ev) => set("label", ev.target.value || null)} aria-label="Label"
            className="rounded-md border border-input bg-surface-1 px-2 py-1.5 text-[13px]">
            <option value="">Every label</option>
            {OTHER_LABELS.map((l) => <option key={l} value={l}>{MAIL_LABELS[l]}</option>)}
          </select>
        )}
        <Input value={q} onChange={(ev) => setQ(ev.target.value)} placeholder="Search subject or sender" className="max-w-[320px]" aria-label="Search mail" />
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the mail" />
      {data && (
        <Card className="overflow-hidden">
          {data.length === 0 ? <Empty>Nothing here.</Empty> : (
            <div className="divide-y divide-line">
              {data.map((m) => <MailRow key={m.id} m={m} open={openId === m.id} onToggle={() => setOpenId(openId === m.id ? null : m.id)} />)}
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

function MailRow({ m, open, onToggle }: { m: InboundEmailView; open: boolean; onToggle: () => void }) {
  const canWork = hasPermission(usePermissions(), "enquiries.work")
  return (
    <div>
      <button type="button" onClick={onToggle} className="w-full text-left px-3 py-2.5 hover:bg-surface-2 text-[13px]">
        <div className="flex flex-wrap items-center gap-1.5">
          {m.label && <Tag variant={m.label === "complaint" ? "red" : "neutral"}>{MAIL_LABELS[m.label] ?? m.label}</Tag>}
          {m.auto_kind && <Tag>{m.auto_kind === "own" ? "Our own mail" : m.auto_kind === "bounce" ? "Bounce" : m.auto_kind === "auto_reply" ? "Auto-reply" : m.auto_kind}</Tag>}
          {m.stage === "failed" && <Tag variant="red">Not read</Tag>}
          {m.relabelled_at && <Tag variant="teal">Labelled by a person</Tag>}
          {m.provider === "test" && <Tag variant="gold">Test</Tag>}
          <span className="text-ink-700 font-medium">{m.from_name ?? m.from_address}</span>
          <span className="flex-1" />
          <span className="text-ink-300 text-[12px]">{formatDateTime(m.received_at)}</span>
        </div>
        <div className="mt-0.5 min-w-0 truncate">
          <span className="font-semibold text-ink-900">{m.subject || "(no subject)"}</span>
          <span className="text-ink-500"> · {(m.body ?? "").replace(/\s+/g, " ").slice(0, 140)}</span>
        </div>
      </button>
      {open && <MailDetail id={m.id} canWork={canWork} />}
    </div>
  )
}

function MailDetail({ id, canWork }: { id: string; canWork: boolean }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data: m, isLoading } = useQuery({
    queryKey: ["enquiries", "mail-one", id],
    queryFn: async () => (await api.get<InboundEmailView>(`/enquiries/mail/${id}`)).data,
  })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["enquiries", "mail"] })
    qc.invalidateQueries({ queryKey: ["enquiries", "counts"] })
    qc.invalidateQueries({ queryKey: ["enquiries", "mail-one", id] })
  }
  const relabel = useMutation({ mutationFn: async (label: string) => api.post(`/enquiries/mail/${id}/label`, { label }), onSuccess: refresh })
  const convert = useMutation({
    mutationFn: async () => (await api.post<{ enquiry_id: string; ref: string }>(`/enquiries/mail/${id}/convert`)).data,
    onSuccess: (r) => { refresh(); qc.invalidateQueries({ queryKey: ["enquiries"] }); navigate(`/enquiries/${r.enquiry_id}`) },
  })
  const retry = useMutation({ mutationFn: async () => api.post(`/enquiries/mail/${id}/retry`), onSuccess: refresh })
  if (isLoading || !m) return <div className="px-3 pb-3"><Loading /></div>
  const err = relabel.error ?? convert.error ?? retry.error
  return (
    <div className="px-3 pb-3 space-y-2 bg-surface-0">
      <p className="text-[12px] text-ink-500">From {m.from_name ? `${m.from_name} <${m.from_address}>` : m.from_address} · to {m.to.join(", ") || "–"}</p>
      {m.label_source === "person"
        ? <p className="font-mono text-[10.5px] text-teal-600">◆ Labelled by a person {formatDateTime(m.relabelled_at)}</p>
        : m.label_reason && <p className="font-mono text-[10.5px] text-teal-600">◆ {m.label_source === "model" ? "The AI" : m.label_source === "local" ? "Local classifier" : "The rules (not the AI)"}: {m.label_reason}</p>}
      {m.original_label && m.original_label !== m.label && <p className="text-[12px] text-ink-500">First read as {MAIL_LABELS[m.original_label] ?? m.original_label}.</p>}
      {m.last_error && <p className="text-[12px] text-red-500">{m.last_error}</p>}
      <pre className="whitespace-pre-wrap font-body text-[13px] text-ink-900 max-h-[360px] overflow-auto rounded border border-line bg-surface-1 p-2">{m.body || "(empty)"}</pre>
      {m.attachments.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {m.attachments.map((f, i) => f.file_id ? (
            <button key={i} type="button" onClick={() => downloadFile(f.file_id!, f.filename)} className="rounded-md border border-line px-2.5 py-1 text-[12.5px] text-teal-600 hover:bg-teal-100">⤓ {f.filename}</button>
          ) : <span key={i} className="text-[12.5px] text-ink-300">{f.filename} · not kept</span>)}
        </div>
      )}
      {err && <p className="text-red-500 text-[12.5px]">{errorMessage(err, "That did not work")}</p>}
      {canWork && (
        <div className="flex flex-wrap items-center gap-2">
          {m.enquiry_id ? (
            <Button size="sm" variant="secondary" onClick={() => navigate(`/enquiries/${m.enquiry_id}`)}>Open its enquiry</Button>
          ) : m.stage === "done" && (
            <Button size="sm" onClick={() => convert.mutate()} disabled={convert.isPending}>{convert.isPending ? "Making it an enquiry…" : "Make it an enquiry"}</Button>
          )}
          {m.stage === "failed" && <Button size="sm" onClick={() => retry.mutate()} disabled={retry.isPending}>Try reading it again</Button>}
          {m.stage === "done" && !m.auto_kind && (
            <>
              <span className="text-[12px] text-ink-500 ml-2">Label it:</span>
              {OTHER_LABELS.filter((l) => l !== m.label || m.outcome === "review").map((l) => (
                <button key={l} type="button" onClick={() => relabel.mutate(l)} disabled={relabel.isPending}
                  className="rounded-full border border-line px-2.5 py-0.5 text-[12px] text-ink-500 hover:text-ink-900 hover:border-teal-500">
                  {MAIL_LABELS[l]}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
