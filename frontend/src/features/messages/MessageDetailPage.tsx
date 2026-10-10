import { useEffect, useState } from "react"
import { Link, useLocation, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Input } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { useLanguages, useMessage } from "./hooks"
import {
  CHANNEL_LABELS, CHECK_LABELS, PURPOSE_LABELS, REVIEW_WHY, SOURCE_LABELS, STATUS_TONE, actionsFor, partyLabel, waitLabel,
} from "./labels"
import { type EditorValue, MessageEditor } from "./MessageEditor"
import type { MessageDetail, MessageEvent, MessageView } from "./types"

function eventLine(e: MessageEvent): string {
  const d = e.detail ?? {}
  const bits: string[] = []
  if (d.sent_anyway) bits.push("sent anyway by a person")
  if (d.resend) bits.push("to send again")
  if (typeof d.retry_in_min === "number") bits.push(`trying again in ${d.retry_in_min} min`)
  if (typeof d.reason === "string" && d.reason) bits.push(d.reason)
  if (typeof d.check === "string") bits.push(`stopped at: ${CHECK_LABELS[d.check] ?? d.check}`)
  if (d.stuck) bits.push("no answer from the mail server, so Forge cannot tell whether it arrived")
  if (d.after_marked_lost) bits.push("the mail server confirmed it after all")
  if (typeof d.matched === "string") bits.push(d.matched)
  return bits.join("; ")
}

/** What the customer sees: the text, the AI marker and, on promotional emails, the unsubscribe line. */
export function CustomerView({ m }: { m: MessageView }) {
  return (
    <div className="rounded-md bg-surface-2 p-3 text-[13px] text-ink-900 space-y-2">
      <p className="whitespace-pre-wrap">{m.body}</p>
      {m.direction === "out" && m.ai_generated && m.disclosure && <p className="text-ink-500 italic">{m.disclosure}</p>}
      {m.direction === "out" && m.purpose === "promotional" && (
        <p className="text-ink-300 text-[12px]">Below it: a line with the link to stop promotional emails, in the message's language.</p>
      )}
    </div>
  )
}

function DraftEditor({ m }: { m: MessageDetail }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const langs = useLanguages()
  const location = useLocation()
  const written = location.state as { problems?: string[]; rewritten?: boolean } | null
  const [v, setV] = useState<EditorValue>({
    subject: m.subject ?? "", body: m.body, language: m.language ?? "",
    purpose: (m.purpose === "staff" || !m.purpose ? "transactional" : m.purpose),
  })
  const changed = v.subject !== (m.subject ?? "") || v.body !== m.body || v.language !== (m.language ?? "") || v.purpose !== m.purpose
  const refresh = (data: MessageView) => {
    qc.setQueryData(["message", m.id], { ...m, ...data })
    qc.invalidateQueries({ queryKey: ["message", m.id] })
    qc.invalidateQueries({ queryKey: ["messages"] })
  }
  const save = useMutation({
    mutationFn: async () => (await api.patch<MessageView>(`/messages/${m.id}`, {
      subject: v.subject, body: v.body, purpose: v.purpose, language: v.language || undefined })).data,
    onSuccess: refresh,
  })
  const send = useMutation({
    mutationFn: async () => {
      if (changed) await save.mutateAsync()
      return (await api.post<MessageView>(`/messages/${m.id}/send`)).data
    },
    onSuccess: refresh,
  })
  const cancel = useMutation({
    mutationFn: async () => (await api.post<MessageView>(`/messages/${m.id}/cancel`)).data,
    onSuccess: (d) => { refresh(d); navigate("/messages") },
  })
  return (
    <div className="space-y-3">
      {m.ai_generated && (
        <div className="rounded-md bg-teal-100 p-2 text-[12.5px] text-ink-700 space-y-1">
          <p>Written by Forge's Writing Assistant{written?.rewritten ? ", which rewrote it once after its checks" : ""}. Read it and change anything before sending.
            The customer sees “{m.disclosure}” under it.</p>
          {written?.problems?.map((p) => <p key={p} className="text-amber-500">Still to fix: {p}</p>)}
        </div>
      )}
      <MessageEditor value={v} onChange={setV} languages={langs.data} isReply={!!m.in_reply_to_id} />
      <ErrorText error={save.error ?? send.error ?? cancel.error} fallback="Could not do that" />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => send.mutate()} disabled={send.isPending || !v.body.trim() || !v.subject.trim()}>
          {send.isPending ? "Sending…" : "Send"}
        </Button>
        <Button variant="secondary" onClick={() => save.mutate()} disabled={!changed || save.isPending}>Save draft</Button>
        <Button variant="danger" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Discard</Button>
        {save.isSuccess && !changed && <span className="text-[12.5px] text-green-500 self-center">Saved.</span>}
      </div>
    </div>
  )
}

function Actions({ m }: { m: MessageDetail }) {
  const qc = useQueryClient()
  const a = actionsFor(m)
  const [why, setWhy] = useState("")
  const [anyway, setAnyway] = useState(false)
  const done = (data: MessageView) => {
    qc.setQueryData(["message", m.id], { ...m, ...data })
    qc.invalidateQueries({ queryKey: ["message", m.id] })
    qc.invalidateQueries({ queryKey: ["messages"] })
  }
  const act = useMutation({
    mutationFn: async (kind: "cancel" | "resend" | "send-anyway") => (await api.post<MessageView>(
      `/messages/${m.id}/${kind}`, kind === "send-anyway" ? { reason: why.trim() } : undefined)).data,
    onSuccess: (d) => { setAnyway(false); setWhy(""); done(d) },
  })
  if (!a.cancel && !a.sendAnyway && !a.resend && !a.reply) return null
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {a.reply && <Link to={`/messages/new?reply_to=${m.id}`}><Button size="sm">Reply</Button></Link>}
        {a.sendAnyway && !anyway && <Button size="sm" variant="gold" onClick={() => setAnyway(true)}>Send it now anyway</Button>}
        {a.resend && (
          <Button size="sm" onClick={() => act.mutate("resend")} disabled={act.isPending}
            title="Check first that the customer did not get it">
            Send it again
          </Button>
        )}
        {a.cancel && <Button size="sm" variant="danger" onClick={() => act.mutate("cancel")} disabled={act.isPending}>Cancel it</Button>}
      </div>
      {anyway && (
        <form className="rounded-md bg-gold-100 p-2 space-y-2 text-[12.5px]" onSubmit={(e) => { e.preventDefault(); act.mutate("send-anyway") }}>
          <p>It goes now, past quiet hours and the limits; your name and reason are recorded. Consent and stops still apply.</p>
          <Input autoFocus placeholder="Why now? e.g. the customer asked for the quote tonight" value={why} onChange={(e) => setWhy(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" variant="gold" type="submit" disabled={why.trim().length < 5 || act.isPending}>Send now</Button>
            <Button size="sm" variant="secondary" type="button" onClick={() => setAnyway(false)}>Back</Button>
          </div>
        </form>
      )}
      <ErrorText error={act.error} fallback="Could not do that" />
    </div>
  )
}

export function MessageDetailPage() {
  const { messageId } = useParams()
  const perms = usePermissions()
  const canSend = hasPermission(perms, "messages.send")
  const canTrace = hasPermission(perms, "audit.read")
  const { data: m, isLoading, error } = useMessage(messageId)
  const [, tick] = useState(0)
  useEffect(() => { const t = window.setInterval(() => tick((x) => x + 1), 30_000); return () => window.clearInterval(t) }, [])
  if (isLoading) return <Loading />
  if (error || !m) return <ErrorText error={error} fallback="Could not load the message" />
  const editing = canSend && actionsFor(m).edit
  const wait = waitLabel(m)
  const stopped = ["failed", "bounced", "blocked"].includes(m.status)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-4 items-start">
      <div className="space-y-4 min-w-0">
        <Card>
          <CardHeader className="space-y-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <Tag>{CHANNEL_LABELS[m.channel] ?? m.channel}</Tag>
              <Tag variant={STATUS_TONE[m.status] ?? "neutral"}>{m.status_label}</Tag>
              {m.purpose && <Tag>{PURPOSE_LABELS[m.purpose] ?? m.purpose}</Tag>}
              {m.ai_generated && <Tag variant="teal">Written with AI</Tag>}
              {m.provider === "simulator" && <Tag variant="gold">Test inbox</Tag>}
            </div>
            <CardTitle className="text-[17px]">{m.subject || "(no subject)"}</CardTitle>
            <p className="text-[12.5px] text-ink-500">
              {partyLabel(m)}
              {m.direction === "out" && m.to_address && ` (${m.to_address})`}
              {m.direction === "in" && m.from_address && m.contact_name && ` (${m.from_address})`}
              {" · "}{m.language_name ?? "language not recognised"}
              {" · "}{m.sent_at ? `sent ${formatDateTime(m.sent_at)}` : `${m.direction === "in" ? "received" : "written"} ${formatDateTime(m.created_at)}`}
            </p>
            {m.direction === "out" && (
              <p className="text-[12px] text-ink-300">
                Written by <Actor actor={m.created_by} />
                {m.override_by && <> · sent anyway by <Actor actor={m.override_by} /></>}
                {m.attempts > 1 && ` · ${m.attempts} tries`}
              </p>
            )}
          </CardHeader>
          <CardContent className="space-y-3">
            {(wait || stopped) && (
              <p className={cn("rounded-md p-2 text-[12.5px]", stopped ? "bg-red-100 text-red-500" : "bg-amber-100 text-ink-700")}>
                {wait ?? m.reason}
              </p>
            )}
            {editing ? <DraftEditor m={m} /> : <CustomerView m={m} />}
            {canSend && !editing && <Actions m={m} />}
            {m.erased_at && <p className="text-[12px] text-ink-500">The person's details were erased on {formatDateTime(m.erased_at)}.</p>}
          </CardContent>
        </Card>

        {m.thread.length > 0 && (
          <Card>
            <CardHeader><CardTitle>Conversation</CardTitle></CardHeader>
            <CardContent>
              <ul className="space-y-1 text-[13px]">
                {m.thread.map((t) => (
                  <li key={t.id} className="flex flex-wrap gap-2">
                    <Tag variant={t.direction === "in" ? "teal" : "neutral"}>{t.direction === "in" ? "Their message" : "Our message"}</Tag>
                    <Link to={`/messages/${t.id}`} className="text-teal-600 hover:underline">{t.subject || "(no subject)"}</Link>
                    <span className="text-ink-300">{formatDateTime(t.created_at)}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {m.reviews.length > 0 && (
          <Card>
            <CardHeader><CardTitle>Language reviews</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-[13px]">
              {m.reviews.map((r) => (
                <div key={r.id} className="rounded-md bg-surface-2 p-2 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Tag variant={r.status === "waiting" ? "gold" : r.status === "rejected" ? "red" : r.status === "changed" ? "amber" : "green"}>{r.status}</Tag>
                    <span className="text-ink-500">{REVIEW_WHY[r.why] ?? r.why}{r.blocking ? ", waits for it" : ", after it went"}</span>
                    {r.score !== null && <span className="text-ink-700">Score {r.score} / 5</span>}
                  </div>
                  {r.problems.map((p) => <p key={p} className="text-amber-500 text-[12.5px]">{p}</p>)}
                  {r.comment && <p className="text-ink-700">“{r.comment}” <span className="text-ink-300">· <Actor actor={r.reviewed_by} /></span></p>}
                  {r.original_body && (
                    <details className="text-[12.5px]"><summary className="cursor-pointer text-ink-500">What the AI wrote before the correction</summary>
                      <p className="whitespace-pre-wrap mt-1 text-ink-700">{r.original_body}</p></details>
                  )}
                  {r.status === "waiting" && <Link to="/messages/reviews" className="text-teal-600 text-[12.5px] hover:underline">Open Language review</Link>}
                </div>
              ))}
            </CardContent>
          </Card>
        )}
      </div>

      <div className="space-y-4">
        {m.checks.length > 0 && (
          <Card>
            <CardHeader><CardTitle>Checks</CardTitle></CardHeader>
            <CardContent>
              <ul className="space-y-1.5 text-[12.5px]">
                {m.checks.map((c) => (
                  <li key={c.check} className="flex gap-2">
                    <span className={cn("shrink-0 w-4 font-bold", c.ok ? "text-green-500" : "text-red-500")}>{c.ok ? "✓" : "✕"}</span>
                    <span><b className="text-ink-700">{CHECK_LABELS[c.check] ?? c.check}.</b> <span className="text-ink-500">{c.detail}</span></span>
                  </li>
                ))}
              </ul>
              <p className="text-[11.5px] text-ink-300 mt-2">Checked when it was queued and again just before it went.</p>
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader><CardTitle>What happened</CardTitle></CardHeader>
          <CardContent>
            <ol className="space-y-2 text-[12.5px]">
              {m.events.map((e, i) => {
                const line = eventLine(e)
                return (
                  <li key={i} className={cn("border-l-2 pl-2", e.applied ? "border-teal-500" : "border-line")}>
                    <div className="flex flex-wrap gap-x-2">
                      <b className="text-ink-900">{e.label}</b>
                      <span className="text-ink-300">{formatDateTime(e.at)}</span>
                    </div>
                    <div className="text-ink-500">
                      by {e.actor ? <Actor actor={e.actor} /> : SOURCE_LABELS[e.source] ?? e.source}
                      {line && ` · ${line}`}
                      {!e.applied && " · recorded, the status did not change"}
                    </div>
                  </li>
                )
              })}
            </ol>
          </CardContent>
        </Card>
        {canTrace && (
          <div className="flex flex-col gap-1 text-[12.5px]">
            <Link to={`/agents/messages/${m.id}`} className="text-teal-600 hover:underline">Trace this message in the audit trail</Link>
            {m.run_id && <Link to={`/agents/runs/${m.run_id}`} className="text-teal-600 hover:underline">Replay the run that wrote it</Link>}
          </div>
        )}
      </div>
    </div>
  )
}
