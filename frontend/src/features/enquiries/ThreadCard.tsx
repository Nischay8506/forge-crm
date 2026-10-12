import { useState } from "react"
import { Link } from "react-router-dom"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Tag } from "@/components/ui/Tag"
import { STATUS_TONE as MSG_TONE } from "@/features/messages/labels"
import { cn, formatDateTime } from "@/lib/utils"
import { downloadFile } from "./files"
import { MAIL_LABELS, sizeText } from "./labels"
import type { Attachment, EnquiryDetail, InboundEmailView } from "./types"
import type { MessageView } from "@/features/messages/types"

type Item = { kind: "in"; at: string; email: InboundEmailView } | { kind: "out"; at: string; msg: MessageView }

/** The conversation: the customer's emails and what we sent back, oldest first. */
export function ThreadCard({ e }: { e: EnquiryDetail }) {
  const items: Item[] = [
    ...e.emails.map((m) => ({ kind: "in" as const, at: m.received_at, email: m })),
    ...e.messages.filter((m) => m.direction === "out").map((m) => ({ kind: "out" as const, at: m.created_at, msg: m })),
  ].sort((a, b) => a.at.localeCompare(b.at))
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle>Conversation</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-3">
        {items.length === 0 && <p className="text-ink-300 text-[13px]">Entered by hand: no emails yet.</p>}
        {items.map((it) => it.kind === "in"
          ? <EmailItem key={`in-${it.email.id}`} m={it.email} />
          : <SentItem key={`out-${it.msg.id}`} m={it.msg} />)}
      </div>
    </Card>
  )
}

function Body({ text }: { text: string | null }) {
  const [open, setOpen] = useState(false)
  const t = text ?? ""
  const long = t.length > 900
  return (
    <div>
      <pre className="whitespace-pre-wrap font-body text-[13px] text-ink-900 break-words">{long && !open ? `${t.slice(0, 900)}…` : t}</pre>
      {long && <button type="button" className="text-[12px] text-teal-600 hover:underline" onClick={() => setOpen(!open)}>{open ? "Show less" : "Show all"}</button>}
    </div>
  )
}

function EmailItem({ m }: { m: InboundEmailView }) {
  return (
    <div className="rounded-md border border-line p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
        <Tag>From customer</Tag>
        {m.provider === "test" && <Tag variant="gold">Test mailbox</Tag>}
        {m.label && m.label !== "enquiry" && <Tag variant="amber">{MAIL_LABELS[m.label] ?? m.label}</Tag>}
        <span className="font-semibold text-ink-700">{m.from_name ?? m.from_address}</span>
        {m.from_name && <span className="text-ink-300">{m.from_address}</span>}
        <span className="flex-1" />
        <span className="text-ink-300">{formatDateTime(m.received_at)}</span>
      </div>
      {m.subject && <div className="font-semibold text-ink-900 text-[13.5px]">{m.subject}</div>}
      {m.erased ? <p className="text-ink-300 text-[13px]">Erased on the person's request.</p> : <Body text={m.body} />}
      {m.attachments.length > 0 && <Attachments files={m.attachments} />}
      {m.label_source && (
        <p className="font-mono text-[10.5px] text-teal-600">
          ◆ {m.label_source === "model" ? `Read by the AI${m.label_confidence ? `, ${Math.round(Number(m.label_confidence) * 100)}% sure` : ""}${m.label_reason ? ` · ${m.label_reason}` : ""}`
            : m.label_source === "rules" ? "Read by rules, not by the AI: check it"
            : "Labelled by a person"}
        </p>
      )}
    </div>
  )
}

function Attachments({ files }: { files: Attachment[] }) {
  const [err, setErr] = useState<string | null>(null)
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-2">
        {files.map((f, i) => f.file_id ? (
          <button key={i} type="button" onClick={() => { setErr(null); downloadFile(f.file_id!, f.filename).catch(() => setErr(`Could not download ${f.filename}`)) }}
            className="rounded-md border border-line bg-surface-0 px-2.5 py-1 text-[12.5px] text-teal-600 hover:bg-teal-100">
            ⤓ {f.filename} <span className="text-ink-300">{sizeText(f.size)}</span>
          </button>
        ) : (
          <span key={i} title={f.skipped ?? undefined} className="rounded-md border border-line px-2.5 py-1 text-[12.5px] text-ink-300">
            {f.filename} · not kept
          </span>
        ))}
      </div>
      {err && <p className="text-red-500 text-[12px]">{err}</p>}
    </div>
  )
}

function SentItem({ m }: { m: MessageView }) {
  return (
    <div className={cn("rounded-md border p-3 space-y-2 ml-4 sm:ml-10", m.ai_generated ? "border-teal-500/40 bg-teal-100/30" : "border-line bg-surface-0")}>
      <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
        <Tag variant="teal">We sent</Tag>
        <Tag variant={MSG_TONE[m.status] ?? "neutral"}>{m.status_label ?? m.status}</Tag>
        {m.ai_generated && <Tag variant="teal">AI</Tag>}
        {m.provider === "simulator" && <Tag variant="gold">Test inbox</Tag>}
        <span className="flex-1" />
        <Link to={`/messages/${m.id}`} className="text-teal-600 hover:underline">{formatDateTime(m.sent_at ?? m.created_at)}</Link>
      </div>
      {m.subject && <div className="font-semibold text-ink-900 text-[13.5px]">{m.subject}</div>}
      <Body text={m.body} />
      {m.disclosure && <p className="text-[11.5px] text-ink-300">{m.disclosure}</p>}
      {m.reason && ["failed", "bounced", "blocked"].includes(m.status) && <p className="text-[12px] text-red-500">{m.reason}</p>}
    </div>
  )
}
