import { useEffect, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Input, Select } from "@/components/ui/Field"
import { Nameplate } from "@/components/ui/Nameplate"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { useAuthStore } from "@/stores/authStore"
import { AckComposer } from "./AckComposer"
import { CustomerCard } from "./CustomerCard"
import { useEnquiry, usePeople } from "./hooks"
import { LinesCard } from "./LinesCard"
import { MakeCard } from "./MakeCard"
import { ReviewCard } from "./ReviewCard"
import { ScoreCard } from "./ScoreCard"
import { MAIL_LABELS, OTHER_LABELS, RULE_LABELS, STATUS_TONE, eventText } from "./labels"
import { ThreadCard } from "./ThreadCard"
import { TimerChip } from "./TimerChip"
import type { EnquiryDetail } from "./types"

type Panel = null | "ack" | "assign" | "close" | "not_enquiry"

export function EnquiryDetailPage() {
  const { id } = useParams()
  const { data: e, isLoading, error } = useEnquiry(id)
  const me = useAuthStore((s) => s.me)
  const qc = useQueryClient()
  const told = useRef(false)
  // Opening an enquiry you own is your first look at it (F1.10).
  useEffect(() => {
    if (e && me && e.owner_id === me.id && !e.owner_seen_at && !told.current) {
      told.current = true
      api.post(`/enquiries/${e.id}/seen`).then(() => qc.invalidateQueries({ queryKey: ["enquiry", e.id] })).catch(() => {})
    }
  }, [e, me, qc])

  if (isLoading) return <Loading />
  if (error || !e) return <ErrorText error={error} fallback="Could not load the enquiry" />
  return <Detail e={e} />
}

function Detail({ e }: { e: EnquiryDetail }) {
  const qc = useQueryClient()
  const [panel, setPanel] = useState<Panel>(null)
  const act = useMutation({
    mutationFn: async ({ path, body }: { path: string; body?: unknown }) =>
      (await api.post<EnquiryDetail>(`/enquiries/${e.id}/${path}`, body ?? {})).data,
    onSuccess: (data) => {
      qc.setQueryData(["enquiry", e.id], data)
      qc.invalidateQueries({ queryKey: ["enquiries"] })
      setPanel(null)
    },
  })
  const check = useMutation({
    mutationFn: async () => (await api.patch<EnquiryDetail>(`/enquiries/${e.id}`, { checked: true })).data,
    onSuccess: (data) => { qc.setQueryData(["enquiry", e.id], data); qc.invalidateQueries({ queryKey: ["enquiries"] }) },
  })
  const navigate = useNavigate()
  const startQuote = useMutation({
    mutationFn: async () => (await api.post<{ id: string }>("/quotes", { enquiry_id: e.id })).data,
    onSuccess: (q) => {
      qc.invalidateQueries({ queryKey: ["enquiry", e.id] })
      qc.invalidateQueries({ queryKey: ["quotes"] })
      navigate(`/quotes/${q.id}`)
    },
  })
  const open = ["new", "open", "waiting_customer", "parked"].includes(e.status)
  const acked = e.events.some((ev) => ev.kind === "acknowledged")
  const names = Object.fromEntries(e.assignments.filter((a) => a.user_id).map((a) => [a.user_id!, a.user_name ?? ""]))

  return (
    <div className="space-y-4">
      <div className="text-[12.5px]"><Link to="/enquiries" className="text-teal-600 hover:underline">← Enquiries</Link></div>
      <div className="flex flex-wrap items-center gap-2">
        <Nameplate id={e.ref} />
        <Tag variant={STATUS_TONE[e.status]}>{e.status_label}</Tag>
        {e.account_tier === "strategic" && <Tag variant="gold">Strategic</Tag>}
        {e.is_tender && <Tag variant="gold">Tender{e.details.tender_ref ? ` · ${e.details.tender_ref}` : ""}</Tag>}
        <Tag>{e.source === "manual" ? "Manual" : "Email"}</Tag>
        <span className="text-[12px] text-ink-300">{formatDateTime(e.created_at)}</span>
        {e.timer && <span className="lg:hidden"><TimerChip t={e.timer} /></span>}
      </div>
      <div>
        <h1 className="text-xl font-bold text-ink-900">{e.summary ?? "(no summary)"}</h1>
        <p className="text-[13px] text-ink-500">{e.company ?? e.who}{e.company && e.who !== e.company ? ` · ${e.who}` : ""}</p>
      </div>

      {e.merged_into && (
        <Banner tone="neutral">
          Merged into <Link to={`/enquiries/${e.merged_into.id}`} className="underline font-semibold">{e.merged_into.ref}</Link>; its page shows these lines and emails too.
          {e.may.work && <Button size="sm" variant="secondary" className="ml-2" onClick={() => act.mutate({ path: "undo-merge" })}>Undo the merge</Button>}
        </Banner>
      )}
      {e.merged_in.length > 0 && (
        <Banner tone="neutral">
          {e.merged_in.map((m, i) => (
            <span key={m.id}>{i > 0 && ", "}<Link to={`/enquiries/${m.id}`} className="underline font-semibold">{m.ref}</Link></span>
          ))} {e.merged_in.length === 1 ? "was" : "were"} merged into this one.
        </Banner>
      )}
      {e.needs_review && open && (
        <Banner tone="amber">
          <b>Check what was read.</b>
          <ul className="list-disc pl-5 my-1">{e.review_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          {e.may.work && <Button size="sm" onClick={() => check.mutate()} disabled={check.isPending}>It is right</Button>}
        </Banner>
      )}
      {e.duplicate_of && e.possible_duplicate && open && (
        <Banner tone="amber">
          <b>This looks like <Link to={`/enquiries/${e.duplicate_of.id}`} className="underline">{e.duplicate_of.ref}</Link></b>
          {e.duplicate_of.summary ? ` (${e.duplicate_of.summary})` : ""}: {e.possible_duplicate.reasons.join(", ")}.
          {e.may.work && (
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" onClick={() => act.mutate({ path: "duplicate", body: { other_id: e.duplicate_of!.id, same: true } })}>Same enquiry: merge</Button>
              <Button size="sm" variant="secondary" onClick={() => act.mutate({ path: "duplicate", body: { other_id: e.duplicate_of!.id, same: false } })}>Different enquiries</Button>
            </div>
          )}
        </Banner>
      )}
      {e.status === "waiting_customer" && (
        <Banner tone="neutral">
          Waiting on the customer for: {e.questions.filter((q) => !q.answered_at).map((q) => q.label).join(", ") || "an answer"}.
          {e.nudges > 0 && ` ${e.nudges} ${e.nudges === 1 ? "reminder" : "reminders"} sent.`}
        </Banner>
      )}
      {e.status === "closed" && e.close_reason && <Banner tone="neutral">Closed: {e.close_reason}</Banner>}

      {act.error && <p role="alert" className="text-red-500 text-[13px]">{errorMessage(act.error, "That did not work")}</p>}
      {check.error && <p role="alert" className="text-red-500 text-[13px]">{errorMessage(check.error, "That did not work")}</p>}
      {startQuote.error && <p role="alert" className="text-red-500 text-[13px]">{errorMessage(startQuote.error, "Could not start the quote")}</p>}

      <div className="flex flex-wrap gap-2">
        {open && e.may.send && (
          <Button size="sm" variant={acked ? "secondary" : "default"} onClick={() => setPanel(panel === "ack" ? null : "ack")}>
            {acked ? "Send another acknowledgement" : "Acknowledge the customer"}
          </Button>
        )}
        {e.quote && e.may.see_quotes && (
          <Link to={`/quotes/${e.quote.id}`} className="inline-flex items-center rounded-md border border-teal-500 px-3 h-8 text-xs font-semibold text-teal-600 hover:bg-teal-100">
            Open quote {e.quote.number}
          </Link>
        )}
        {open && !e.quote && e.may.quote && (
          <Button size="sm" onClick={() => startQuote.mutate()} disabled={startQuote.isPending}>Start a quote</Button>
        )}
        {open && e.may.work && !e.quote_sent_at && (
          <Button size="sm" variant="secondary" onClick={() => act.mutate({ path: "quote-sent" })} title="Until quotations are built (M3), mark it here">Quote sent</Button>
        )}
        {open && e.may.assign && <Button size="sm" variant="secondary" onClick={() => setPanel(panel === "assign" ? null : "assign")}>Give to someone else</Button>}
        {open && e.may.work && <Button size="sm" variant="secondary" onClick={() => setPanel(panel === "not_enquiry" ? null : "not_enquiry")}>Not an enquiry</Button>}
        {open && e.may.work && <Button size="sm" variant="danger" onClick={() => setPanel(panel === "close" ? null : "close")}>Close</Button>}
        {["closed", "parked"].includes(e.status) && e.may.work && (
          <Button size="sm" variant="secondary" onClick={() => act.mutate({ path: "reopen" })}>Reopen</Button>
        )}
      </div>
      {panel === "ack" && <AckComposer e={e} onClose={() => setPanel(null)} />}
      {panel === "assign" && <AssignPanel e={e} busy={act.isPending} onSave={(user_id, reason) => act.mutate({ path: "assign", body: { user_id, reason } })} onCancel={() => setPanel(null)} />}
      {panel === "close" && <ReasonPanel title="Why is it closed?" placeholder="e.g. Customer bought elsewhere; not a product we make" action="Close the enquiry" busy={act.isPending}
        onSave={(reason) => act.mutate({ path: "close", body: { reason } })} onCancel={() => setPanel(null)} />}
      {panel === "not_enquiry" && <NotEnquiryPanel busy={act.isPending} onSave={(label) => act.mutate({ path: "not-enquiry", body: { label } })} onCancel={() => setPanel(null)} />}

      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <div className="space-y-4 min-w-0">
          {/* On a phone, the priority and the check come first; on a wide screen they sit on the right. */}
          <div className="space-y-4 lg:hidden"><ScoreCard e={e} /><MakeCard e={e} /></div>
          {e.review && <ReviewCard e={e} />}
          <LinesCard e={e} />
          <ThreadCard e={e} />
          <Card>
            <CardHeader className="pb-2"><CardTitle>History</CardTitle></CardHeader>
            <ol className="px-4 pb-4 space-y-1.5 text-[12.5px]">
              {[...e.events].reverse().map((ev, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-ink-300 whitespace-nowrap w-[130px] shrink-0">{formatDateTime(ev.at)}</span>
                  <span className="text-ink-900">{eventText(ev, names)}<span className="text-ink-300"> · {ev.actor_name}</span></span>
                </li>
              ))}
            </ol>
          </Card>
        </div>
        <div className="space-y-4">
          <div className="hidden lg:block space-y-4"><ScoreCard e={e} /><MakeCard e={e} /></div>
          <Card>
            <CardHeader className="pb-2"><CardTitle>Owner</CardTitle></CardHeader>
            <div className="px-4 pb-4 text-[13px] space-y-1">
              <div className="font-semibold text-ink-900">{e.owner_name ?? <span className="text-amber-500">No owner yet</span>}</div>
              {e.assigned_rule && <div className="text-ink-500">By: {RULE_LABELS[e.assigned_rule] ?? e.assigned_rule}</div>}
              {e.assignments.length > 0 && <div className="text-ink-500">{e.assignments[e.assignments.length - 1].reason}</div>}
              {e.owner_seen_at && <div className="text-ink-300">Opened by the owner {formatDateTime(e.owner_seen_at)}</div>}
            </div>
          </Card>
          <Card>
            <CardHeader className="pb-2"><CardTitle>Timers</CardTitle></CardHeader>
            <div className="px-4 pb-4 space-y-2 text-[13px]">
              {e.timers.length === 0 && <p className="text-ink-300">No timers.</p>}
              {e.timers.map((t) => (
                <div key={t.stage} className="flex items-center justify-between gap-2">
                  <span className="text-ink-700">{t.label}</span>
                  <TimerChip t={t} />
                </div>
              ))}
              {e.timers.some((t) => t.escalated_at) && (
                <p className="text-[12px] text-red-500">A late stage was passed to the manager{e.timers.some((t) => t.escalated2_at) ? " and the head of department" : ""}.</p>
              )}
            </div>
          </Card>
          <CustomerCard key={`${e.id}-${e.customer.company ?? ""}-${e.contact?.id ?? ""}`} e={e} />
          {e.questions.length > 0 && (
            <Card>
              <CardHeader className="pb-2"><CardTitle>Asked of the customer</CardTitle></CardHeader>
              <ul className="px-4 pb-4 space-y-1 text-[13px]">
                {e.questions.map((q) => (
                  <li key={q.key}>
                    <div className="flex justify-between gap-2">
                      <span>{q.label}</span>
                      {q.answered_at ? <Tag variant="green">Answered</Tag> : <Tag variant="amber">Waiting</Tag>}
                    </div>
                    {q.answer && <p className="text-[12px] text-ink-500 break-words">“{q.answer}”</p>}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}

function Banner({ tone, children }: { tone: "amber" | "neutral"; children: React.ReactNode }) {
  return (
    <div role="status" className={tone === "amber" ? "rounded-md bg-amber-100 text-ink-700 text-[13px] p-3" : "rounded-md bg-surface-2 text-ink-700 text-[13px] p-3"}>
      {children}
    </div>
  )
}

function AssignPanel({ e, busy, onSave, onCancel }: { e: EnquiryDetail; busy: boolean; onSave: (userId: string, reason: string) => void; onCancel: () => void }) {
  const people = usePeople()
  const [who, setWho] = useState("")
  const [reason, setReason] = useState("")
  return (
    <Card className="p-4 space-y-2 border-teal-500">
      <p className="text-[13px] font-semibold text-ink-900">Give it to someone else</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <Select value={who} onChange={(ev) => setWho(ev.target.value)} aria-label="Person">
          <option value="">Choose a person…</option>
          {people.data?.filter((p) => p.is_active && p.id !== e.owner_id).map((p) => (
            <option key={p.id} value={p.id}>{p.full_name} · {p.open} open{p.away ? " · away" : ""}</option>
          ))}
        </Select>
        <Input value={reason} onChange={(ev) => setReason(ev.target.value)} placeholder="Why (needed)" maxLength={300} aria-label="Reason" />
      </div>
      <p className="text-[11.5px] text-ink-300">If people change the owner a rule picked three times in 30 days, Forge suggests checking that rule.</p>
      <div className="flex gap-2">
        <Button size="sm" disabled={!who || !reason.trim() || busy} onClick={() => onSave(who, reason.trim())}>Give it</Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </Card>
  )
}

function ReasonPanel({ title, placeholder, action, busy, onSave, onCancel }: {
  title: string; placeholder: string; action: string; busy: boolean; onSave: (reason: string) => void; onCancel: () => void
}) {
  const [reason, setReason] = useState("")
  return (
    <Card className="p-4 space-y-2 border-teal-500">
      <p className="text-[13px] font-semibold text-ink-900">{title}</p>
      <Input value={reason} onChange={(ev) => setReason(ev.target.value)} placeholder={placeholder} maxLength={300} aria-label="Reason" />
      <div className="flex gap-2">
        <Button size="sm" disabled={!reason.trim() || busy} onClick={() => onSave(reason.trim())}>{action}</Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </Card>
  )
}

function NotEnquiryPanel({ busy, onSave, onCancel }: { busy: boolean; onSave: (label: string) => void; onCancel: () => void }) {
  const [label, setLabel] = useState("")
  return (
    <Card className="p-4 space-y-2 border-teal-500">
      <p className="text-[13px] font-semibold text-ink-900">What is it?</p>
      <p className="text-[12px] text-ink-500">The enquiry is closed and its email goes to Other mail with this label. Forge remembers the correction.</p>
      <div className="flex flex-wrap gap-2">
        {OTHER_LABELS.map((l) => (
          <button key={l} type="button" onClick={() => setLabel(l)}
            className={label === l ? "rounded-full border border-teal-500 bg-teal-100 px-3 py-1 text-[12.5px]" : "rounded-full border border-line px-3 py-1 text-[12.5px] text-ink-500"}>
            {MAIL_LABELS[l]}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <Button size="sm" disabled={!label || busy} onClick={() => onSave(label)}>Move to Other mail</Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </Card>
  )
}
