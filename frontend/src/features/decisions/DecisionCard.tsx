import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import axios from "axios"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { AgentBadge } from "@/components/ui/AgentBadge"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Input, Select } from "@/components/ui/Field"
import { ErrorText } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDateTime } from "@/lib/utils"
import { hasPermission } from "@/lib/permissions"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { STATUS_TONES, auditLine } from "@/features/agents/labels"
import type { CardHistory, DecisionCard, DecisionStepInfo, Delegation } from "@/features/agents/types"
import { useColleagues, useNow } from "./hooks"
import { OPTION_LABELS, STEP_STATUS, agoLabel, dueLabel, tallyLabel } from "./labels"
import { durationLabel, markAction, useCardTiming } from "./timing"

type CounterResponse = DecisionCard & {
  counter_result: { status: string; summary: string; reasons: string[]; error: string | null }
  new_card?: DecisionCard
}

/** Where the person can look at the record the decision is about. */
function recordLink(d: DecisionCard): { to: string; label: string } | null {
  const cand = d.args.candidate_id
  if (d.tool_key === "steward.merge_records" && typeof cand === "string" && d.status === "pending") {
    return { to: `/steward/review/${cand}`, label: "Compare the two records" }
  }
  if (d.record_type === "enquiry" && d.record_id) {
    return { to: `/enquiries/${d.record_id}`, label: d.record_name ?? "Open the enquiry" }
  }
  if (d.record_type === "account" && d.record_id) {
    return { to: `/accounts/${d.record_id}`, label: d.record_name ?? "Open account" }
  }
  if (d.record_type === "erp_connection" && d.record_id) {
    if (d.kind === "task" && typeof d.args.entity === "string") {
      return { to: `/integrations/connections/${d.record_id}?tab=failed`, label: "Open the records that failed" }
    }
    if (d.kind === "task" && "waiting" in d.args) {
      return { to: `/integrations/customers?connection=${d.record_id}`, label: "Match the customers" }
    }
    return { to: `/integrations/connections/${d.record_id}`, label: d.record_name ?? "Open the connection" }
  }
  return null
}

/** Card colours: gold = a person decides, teal = an agent acted, red = something is broken. */
const KIND_STYLE: Record<DecisionCard["kind"], { border: string; tag: "gold" | "teal" | "red" | "amber" }> = {
  approval: { border: "border-t-gold-500", tag: "gold" },
  notice: { border: "border-t-teal-500", tag: "teal" },
  alert: { border: "border-t-red-500", tag: "red" },
  task: { border: "border-t-amber-500", tag: "amber" },
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1 border-b border-dashed border-line last:border-0 text-[12.5px]">
      <span className="text-ink-500 shrink-0">{k}</span>
      <span className="text-ink-900 text-right min-w-0 break-words">{children}</span>
    </div>
  )
}

function Banner({ tone, children }: { tone: "teal" | "amber" | "red" | "neutral"; children: React.ReactNode }) {
  const style = {
    teal: "bg-teal-100 text-teal-600", amber: "bg-[#fdf1de] text-[#8a5300]",
    red: "bg-[#fbe9e7] text-[#c0392b]", neutral: "bg-surface-2 text-ink-700",
  }[tone]
  return <p className={cn("text-[12px] rounded-md px-2 py-1.5", style)}>{children}</p>
}

/** "Due in 40 min" (amber under an hour, red once past). */
function DueBadge({ d, now }: { d: DecisionCard; now: number }) {
  if (!d.due_at) return null
  const left = new Date(d.due_at).getTime() - now
  const tone = d.past_deadline || left < 0 ? "bg-[#fbe9e7] text-[#c0392b]"
    : left < 60 * 60_000 ? "bg-[#fdf1de] text-[#c77700]" : "bg-surface-2 text-ink-500"
  return <span className={cn("text-[11px] font-semibold rounded px-1.5 py-0.5 shrink-0", tone)}
    title={`Deadline ${formatDateTime(d.due_at)}`}>{dueLabel(d.due_at, now)}</span>
}

const STEP_MARK: Record<DecisionStepInfo["status"], { mark: string; cls: string }> = {
  approved: { mark: "✓", cls: "bg-[#e2f3ea] text-[#1e8e5a]" },
  pending: { mark: "●", cls: "bg-[#f8f1dc] text-[#8a6d10]" },
  waiting: { mark: "○", cls: "bg-surface-2 text-ink-500" },
  rejected: { mark: "✕", cls: "bg-[#fbe9e7] text-[#c0392b]" },
  countered: { mark: "⇄", cls: "bg-[#fdf1de] text-[#c77700]" },
  cancelled: { mark: "–", cls: "bg-surface-2 text-ink-300 line-through" },
  superseded: { mark: "–", cls: "bg-surface-2 text-ink-300" },
}

/** H1: the chain, step by step. */
function ChainProgress({ steps }: { steps: DecisionStepInfo[] }) {
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[11px]" aria-label="Approval steps">
      {steps.map((s, i) => (
        <li key={s.position} className="flex items-center gap-1">
          {i > 0 && <span className="text-ink-300">→</span>}
          <span className={cn("rounded px-1.5 py-0.5 font-medium", STEP_MARK[s.status].cls)}
            title={`Step ${s.position}: ${STEP_STATUS[s.status]}`}>
            {STEP_MARK[s.status].mark} {s.role ?? "Anyone"}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** H6: the account's history and its ERP figures, with how current the copy is
 * (safeguard 5: stale figures are never shown as current). */
function History({ h, now }: { h: CardHistory; now: number }) {
  if (!h.account) return null
  const erp = h.erp
  const earlier = h.earlier
  return (
    <>
      <Row k="Customer history">
        {h.tier && <>{h.tier} · </>}
        {earlier && earlier.count > 0 ? `${earlier.count} earlier card${earlier.count === 1 ? "" : "s"} this year (${tallyLabel(earlier.by_status)})` : "No earlier cards this year"}
      </Row>
      {erp?.linked && (erp.figures ?? []).map((f) => (
        <Row key={f.currency} k={`In the ERP${(erp.figures?.length ?? 0) > 1 ? ` (${f.currency})` : ""}`}>
          {f.outstanding} outstanding · <span className={f.overdue.match(/[1-9]/) ? "text-red-500 font-semibold" : ""}>{f.overdue} overdue</span> · {f.invoiced_12m} invoiced in 12 months
        </Row>
      ))}
      {erp?.linked && (
        erp.stale ? (
          <Banner tone="amber">
            {erp.as_of ? `ERP figures last read ${agoLabel(erp.as_of, now)} (${formatDateTime(erp.as_of)}), so they may be out of date.`
              : "ERP figures have not been read yet."} Check {erp.erps?.join(", ") ?? "the ERP"} before relying on them.
          </Banner>
        ) : (
          <p className="text-[11px] text-ink-300 text-right">
            From {erp.erps?.join(", ")}, read {erp.as_of ? `${agoLabel(erp.as_of, now)} (${formatDateTime(erp.as_of)})` : "–"}
          </p>
        )
      )}
    </>
  )
}

/** A message the agent wants to send (an acknowledgement, a reminder, a decline): the
 *  person deciding reads exactly what would go out. */
function MessagePreview({ args }: { args: Record<string, unknown> }) {
  const [all, setAll] = useState(false)
  if (typeof args.body !== "string") return null
  const long = args.body.split("\n").length > 6 || args.body.length > 400
  return (
    <div className="py-1 border-b border-dashed border-line text-[12.5px] space-y-0.5">
      <p className="text-ink-500">The message</p>
      {typeof args.subject === "string" && <p className="font-semibold text-ink-900">{args.subject}</p>}
      <p className={cn("whitespace-pre-wrap text-ink-700 bg-surface-2 rounded-md p-2", long && !all && "line-clamp-6")}>{args.body}</p>
      {long && (
        <button type="button" className="text-teal-600 hover:underline" onClick={() => setAll(!all)}>{all ? "Show less" : "Show all of it"}</button>
      )}
    </div>
  )
}

function CounterForm({ d, busy, onSubmit, onCancel }: {
  d: DecisionCard; busy: boolean; onSubmit: (values: Record<string, string>) => void; onCancel: () => void
}) {
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(d.counter_fields.map((f) => [f.key, f.value === null || f.value === undefined ? "" : String(f.value)])))
  const changed = d.counter_fields.filter((f) => values[f.key] !== String(f.value ?? ""))
  return (
    <form className="space-y-2 bg-surface-2 rounded-md p-2" onSubmit={(e) => {
      e.preventDefault()
      onSubmit(Object.fromEntries(changed.map((f) => [f.key, values[f.key]])))
    }}>
      <p className="text-[12px] text-ink-700">Approve a changed version. The change is checked by the policy rules again, and may need more approvals.</p>
      {d.counter_fields.map((f) => (
        <label key={f.key} className="block text-[12px]">
          <span className="text-ink-500">{f.label}{f.key === "body" ? "" : ` (asked: ${d.facts.find((x) => x.key === f.key)?.value ?? String(f.value ?? "–")})`}</span>
          {f.key === "body" ? (
            <textarea value={values[f.key]} rows={8} onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
              className="w-full rounded-md border border-input bg-surface-1 px-3 py-2 text-[13px]" />
          ) : (
            <Input inputMode={f.type === "money" || f.type === "number" ? "decimal" : undefined} value={values[f.key]}
              onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} />
          )}
        </label>
      ))}
      <div className="flex gap-2">
        <Button type="submit" variant="gold" size="sm" disabled={busy || changed.length === 0}>Approve with this change</Button>
        <Button type="button" variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
      {changed.length === 0 && <p className="text-[11.5px] text-ink-500">Change a value above to approve a different one.</p>}
    </form>
  )
}

function HandForm({ d, onDone, onCancel }: { d: DecisionCard; onDone: (dl: Delegation) => void; onCancel: () => void }) {
  const colleagues = useColleagues()
  const [to, setTo] = useState("")
  const [note, setNote] = useState("")
  const hand = useMutation({
    mutationFn: async () => (await api.post<Delegation>(`/decisions/${d.id}/hand`, { to_user_id: to, note: note || null })).data,
    onSuccess: onDone,
  })
  return (
    <form className="space-y-2 bg-surface-2 rounded-md p-2" onSubmit={(e) => { e.preventDefault(); hand.mutate() }}>
      <p className="text-[12px] text-ink-700">They decide this card for you, with no more authority than yours. You can still decide it yourself.</p>
      <Select aria-label="Hand to" value={to} onChange={(e) => setTo(e.target.value)}>
        <option value="">Pick a colleague</option>
        {(colleagues.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </Select>
      <Input placeholder="Note for them (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <ErrorText error={hand.error} fallback="Could not hand the card over" />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!to || hand.isPending}>Hand over</Button>
        <Button type="button" variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  )
}

/** A decision adds to the account's history, which the other waiting cards for that
 * account show (and their fingerprint covers): load those cards again so the person
 * decides what is now true. */
async function refreshSameAccount(qc: ReturnType<typeof useQueryClient>, listKey: unknown[], d: DecisionCard) {
  const acc = d.history?.account?.id
  if (!acc) return
  const others = (qc.getQueryData<DecisionCard[]>(listKey) ?? [])
    .filter((x) => x.id !== d.id && x.status === "pending" && x.history?.account?.id === acc)
  if (!others.length) return
  const fresh = await Promise.all(others.map((x) =>
    api.get<DecisionCard>(`/decisions/${x.id}`).then((r) => r.data).catch(() => null)))
  qc.setQueryData<DecisionCard[]>(listKey, (list) =>
    list?.map((x) => fresh.find((f) => f?.id === x.id) ?? x))
}

function isCardChanged(err: unknown) {
  return axios.isAxiosError(err) && err.response?.data?.error?.code === "card_changed"
}

export function DecisionCardView({ d, listKey, handedTo, highlight }: {
  d: DecisionCard; listKey: unknown[]; handedTo?: string | null; highlight?: boolean
}) {
  const qc = useQueryClient()
  const now = useNow()
  const [note, setNote] = useState("")
  const [showChecks, setShowChecks] = useState(false)
  const [panel, setPanel] = useState<"counter" | "hand" | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const pending = d.status === "pending"
  const approval = d.kind === "approval"
  const timing = useCardTiming(d.id, approval && pending && d.can_decide)

  useEffect(() => {
    if (highlight) timing.ref.current?.scrollIntoView({ behavior: "smooth", block: "center" })
  }, [highlight, timing.ref])

  // Everything a decision can change, except this list (reloading it would drop the
  // decided card, which stays in place showing its outcome until the page is reloaded).
  const refresh = () => {
    for (const k of ["count", "me", "done", "delegations", "detail", "analytics"]) {
      qc.invalidateQueries({ queryKey: ["decisions", k] })
    }
    qc.invalidateQueries({ queryKey: ["candidates"] })
    qc.invalidateQueries({ queryKey: ["accounts"] })
    qc.invalidateQueries({ queryKey: ["integrations"] })
  }
  // The decided card stays where it was; a changed card from a counter goes after it.
  const keep = (updated: DecisionCard, added?: DecisionCard) =>
    qc.setQueryData<DecisionCard[]>(listKey, (list) => list?.flatMap((x) => (x.id !== updated.id ? [x]
      : added && added.status === "pending" ? [updated, added] : [updated])))
  const onError = (err: unknown) => {
    // H7: the card changed since it was shown; load the current one and ask again.
    if (isCardChanged(err)) qc.invalidateQueries({ queryKey: ["decisions"] })
  }

  const act = useMutation({
    mutationFn: async (what: "approve" | "reject" | "acknowledge") =>
      (await api.post<DecisionCard>(`/decisions/${d.id}/${what}`, what === "acknowledge"
        ? undefined : { note: note || null, shown_ms: timing.elapsed(), card_hash: d.card_hash })).data,
    onSuccess: (updated) => {
      markAction()
      keep(updated)
      refreshSameAccount(qc, listKey, d)
      refresh()
    },
    onError,
  })
  const counter = useMutation({
    mutationFn: async (values: Record<string, string>) =>
      (await api.post<CounterResponse>(`/decisions/${d.id}/counter`, {
        values, note: note || null, shown_ms: timing.elapsed(), card_hash: d.card_hash })).data,
    onSuccess: (r) => {
      markAction()
      setPanel(null)
      const next = r.new_card
      setMessage(!next ? r.counter_result.summary
        : next.status === "pending" ? `The changed card now goes to ${next.steps.find((s) => s.status === "pending")?.role ?? "the next approver"}, because the new value needs it.`
          : next.status === "approved" ? "The changed version was approved and carried out."
            : `The changed card ended as ${next.status}: ${r.counter_result.error ?? r.counter_result.reasons.join("; ")}`)
      keep(r, next)
      refreshSameAccount(qc, listKey, d)
      refresh()
    },
    onError,
  })
  const busy = act.isPending || counter.isPending
  const error = act.error ?? counter.error
  const link = recordLink(d)
  const canAudit = hasPermission(usePermissions(), "audit.read")
  const res = d.result
  const style = KIND_STYLE[d.kind] ?? KIND_STYLE.notice
  const multi = d.steps.length > 1

  return (
    <Card ref={timing.ref} data-card={d.id}
      className={cn("p-4 space-y-2 border-t-4", style.border, highlight && "ring-2 ring-teal-500")}>
      <div className="flex items-center justify-between gap-2">
        <Tag variant={style.tag}>{d.tool_title}</Tag>
        {approval && pending ? <DueBadge d={d} now={now} />
          : <span className="text-[11px] text-ink-300">{formatDateTime(d.created_at)}</span>}
      </div>
      <h4 className="text-[14px] font-semibold text-ink-900 leading-snug">{d.title}</h4>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-ink-500">
        <AgentBadge label={d.agent_name.toUpperCase()} />
        {d.requested_by_name && <span>asked by {d.requested_by_name}</span>}
        {approval && !multi && d.steps[0]?.role && <span>· {d.steps[0].role} decides</span>}
        {approval && multi && <span>· step {d.step} of {d.steps.length}</span>}
      </div>
      {approval && multi && <ChainProgress steps={d.steps} />}

      {approval && pending && d.can_decide && d.as_stand_in_for && (
        <Banner tone="teal">You are standing in for {d.as_stand_in_for}. Your decision is recorded as yours, made for them.</Banner>
      )}
      {approval && pending && d.escalated_to_me && (
        <Banner tone="amber">Past its deadline, so you are told. {d.why_not ? `${d.why_not}: please follow up.` : "Please follow up."}</Banner>
      )}
      {approval && pending && handedTo && <Banner tone="neutral">You handed this to {handedTo}. You can still decide it yourself.</Banner>}
      {d.counter && (
        <Banner tone="neutral">
          Countered by {d.counter.by_name ?? "a colleague"}{d.counter.on_behalf_of_name ? ` for ${d.counter.on_behalf_of_name}` : ""}:{" "}
          {d.counter.asked.map((a, i) => <span key={a.key}>{a.label} {a.value} → <b>{d.counter!.countered[i]?.value}</b></span>)}
          {d.counter.note && <> · “{d.counter.note}”</>}
        </Banner>
      )}

      <div>
        {d.facts.map((f) => <Row key={f.key} k={f.label}>{f.value}</Row>)}
        <MessagePreview args={d.args} />
        {d.reasons.length > 0 && (d.kind === "alert" || d.kind === "task" || d.reasons.length > 1 ? (
          <div className="py-1 border-b border-dashed border-line text-[12.5px]">
            {approval && <p className="text-ink-500">Why it is asking</p>}
            <ul className={cn("text-ink-700 space-y-1", approval && "list-disc pl-4")}>
              {d.reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
          </div>
        ) : (
          <Row k={approval ? "Why it is asking" : "Why"}>{d.reasons.join("; ")}</Row>
        ))}
        {d.history && <History h={d.history} now={now} />}
      </div>
      {d.recommendation && (
        <div className="text-[12.5px] bg-teal-100 text-teal-600 rounded-md p-2">
          <span className="font-semibold">Agent recommends{d.recommended_option ? ` ${OPTION_LABELS[d.recommended_option].toLowerCase()}` : ""}: </span>
          {d.recommendation}
        </div>
      )}
      <div className="flex items-center justify-between gap-2 text-[12px]">
        <span className="flex flex-wrap gap-x-3">
          {link && <Link to={link.to} className="text-teal-600 hover:underline">{link.label}</Link>}
          {d.run_id && canAudit && <Link to={`/agents/runs/${d.run_id}`} className="text-teal-600 hover:underline">How this came about</Link>}
        </span>
        {d.checks.length > 0 && (
          <button onClick={() => setShowChecks((s) => !s)} className="text-ink-500 hover:text-ink-900 shrink-0">
            {showChecks ? "Hide checks" : "Show checks"}
          </button>
        )}
      </div>
      {showChecks && (
        <ul className="text-[12px] space-y-1 bg-surface-2 rounded-md p-2">
          {d.checks.map((c) => (
            <li key={c.check} className="flex gap-2">
              <span className={c.ok ? "text-green-500" : "text-red-500"}>{c.ok ? "✓" : "✕"}</span>
              <span><b className="font-semibold">{c.check.replace(/_/g, " ")}</b>: {c.detail}</span>
            </li>
          ))}
        </ul>
      )}

      {approval && pending && (
        d.can_decide ? (
          <div className="space-y-2 pt-1">
            <Input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
            {panel === "counter" && (
              <CounterForm d={d} busy={busy} onSubmit={(v) => counter.mutate(v)} onCancel={() => setPanel(null)} />
            )}
            {panel === "hand" && (
              <HandForm d={d} onCancel={() => setPanel(null)} onDone={(dl) => {
                setPanel(null)
                setMessage(`Handed to ${dl.to_name ?? "your colleague"}. They decide it for you.`)
                qc.invalidateQueries({ queryKey: ["decisions"] })
              }} />
            )}
            {panel === null && (
              <div className="flex flex-wrap gap-2">
                <Button variant="gold" size="sm" disabled={busy} onClick={() => act.mutate("approve")}>Approve</Button>
                {d.counter_fields.length > 0 && (
                  <Button variant="secondary" size="sm" disabled={busy} onClick={() => setPanel("counter")}>Counter…</Button>
                )}
                <Button variant="danger" size="sm" disabled={busy} onClick={() => act.mutate("reject")}>Reject</Button>
                {!d.as_stand_in_for && (
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => setPanel("hand")}>Hand to…</Button>
                )}
              </div>
            )}
          </div>
        ) : (
          !d.escalated_to_me && (
            <p className="text-[12px] text-ink-300">
              {d.why_not ? `${d.why_not}.` : `Waiting for ${d.approver_role?.name ?? "someone who can decide"}.`}
            </p>
          )
        )
      )}
      {d.kind === "notice" && d.status === "open" && (
        <Button variant="secondary" size="sm" disabled={busy} onClick={() => act.mutate("acknowledge")}>Mark as read</Button>
      )}
      {d.kind === "alert" && d.status === "open" && (
        <div className="space-y-1">
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => act.mutate("acknowledge")}>I'm on it</Button>
          <p className="text-[11.5px] text-ink-300">
            {d.tool_key === "inbox.bottleneck" ? "Closes on its own once the cards are decided." : "The alert closes on its own once the connection works again."}
          </p>
        </div>
      )}
      {d.kind === "task" && d.status === "open" && (
        <p className="text-[11.5px] text-ink-300">Closes on its own once every record is dealt with.</p>
      )}
      {busy && <p className="text-[12px] text-ink-500">Working…</p>}
      {error != null && (isCardChanged(error)
        ? <Banner tone="amber">{errorMessage(error, "This card changed; check it and decide again.")}</Banner>
        : <ErrorText error={error} fallback="Could not save the decision" />)}
      {message && <Banner tone="teal">{message}</Banner>}

      {approval && !pending && (
        <div className="text-[12px] space-y-1 pt-1">
          <div className="flex flex-wrap items-center gap-2">
            <Tag variant={STATUS_TONES[d.status] ?? "neutral"}>{d.status}</Tag>
            {d.decided_by_name ? <span className="text-ink-700">{d.decided_by_name}</span> : <Actor actor={d.decided_by} />}
            <span className="text-ink-300">{formatDateTime(d.decided_at)}</span>
          </div>
          {d.note && <p className="text-ink-500">“{d.note}”</p>}
          {d.decision_ms !== null && (
            <p className="text-ink-300">Decided {durationLabel(d.decision_ms)} after the card came on screen</p>
          )}
          {res && !["done", "countered"].includes(res.status) && (
            <p className="text-red-500">Not done: {res.error ?? res.reasons.join("; ")}</p>
          )}
          {d.decided_at && <WhatHappenedNext id={d.id} />}
        </div>
      )}
      {d.kind === "notice" && d.status === "acknowledged" && <Tag variant="neutral">Read</Tag>}
      {(d.kind === "alert" || d.kind === "task") && d.status !== "open" && (
        <div className="text-[12px] flex flex-wrap items-center gap-2">
          <Tag variant={STATUS_TONES[d.status] ?? "neutral"}>{d.status === "acknowledged" ? "someone is on it" : d.status}</Tag>
          {d.decided_by && <Actor actor={d.decided_by} />}
          <span className="text-ink-300">{formatDateTime(d.decided_at)}</span>
          {d.note && <span className="text-ink-500">{d.note}</span>}
        </div>
      )}
    </Card>
  )
}

/** Who decided each step, for whom, and how long they took. */
function StepList({ steps }: { steps: DecisionStepInfo[] }) {
  return (
    <ol className="space-y-1">
      {steps.map((s) => (
        <li key={s.position} className="flex flex-wrap gap-x-2">
          <span className="text-ink-300 w-14 shrink-0">Step {s.position}</span>
          <span className="text-ink-700">
            {s.role ?? "Anyone"}: {STEP_STATUS[s.status]}
            {s.decided_by_name && <> by {s.decided_by_name}</>}
            {s.on_behalf_of_name && <> for {s.on_behalf_of_name}</>}
            {s.decided_at && <span className="text-ink-300"> · {formatDateTime(s.decided_at)}</span>}
            {s.decision_ms !== null && <span className="text-ink-300"> · {durationLabel(s.decision_ms)}</span>}
            {s.overdue_at && <span className="text-red-500"> · after its deadline</span>}
            {s.note && <span className="text-ink-500"> · “{s.note}”</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** F12.3: every decision with its effect: the action carried out (or why not), and
 * anything later that undid or built on it. */
function WhatHappenedNext({ id }: { id: string }) {
  const [open, setOpen] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ["decisions", "detail", id],
    queryFn: async () => (await api.get<DecisionCard>(`/decisions/${id}`)).data,
    enabled: open,
  })
  if (!open) {
    return <button className="text-teal-600 hover:underline" onClick={() => setOpen(true)}>What happened next</button>
  }
  if (isLoading || !data) return <p className="text-ink-300">Loading…</p>
  const next = data.next ?? []
  return (
    <div className="bg-surface-2 rounded-md p-2 space-y-2">
      {data.steps.length > 0 && (
        <div className="space-y-1">
          <p className="font-semibold text-ink-700">Who decided</p>
          <StepList steps={data.steps} />
        </div>
      )}
      {data.countered_by && (
        <p><Link className="text-teal-600 hover:underline" to={`/decisions?card=${data.countered_by}`}>Open the changed card</Link></p>
      )}
      {data.counter_of && (
        <p><Link className="text-teal-600 hover:underline" to={`/decisions?card=${data.counter_of}`}>Open the original card</Link></p>
      )}
      <div className="space-y-1">
        <p className="font-semibold text-ink-700">What happened next</p>
        {next.length === 0 ? <p className="text-ink-300">Nothing recorded after the decision.</p> : (
          <ul className="space-y-1">
            {next.map((r) => (
              <li key={r.seq} className="flex gap-2">
                <span className="text-ink-300 shrink-0 w-32">{formatDateTime(r.created_at)}</span>
                <span className="text-ink-700">{auditLine(r)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
