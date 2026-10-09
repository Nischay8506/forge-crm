import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { Actor } from "@/features/steward/Actor"
import { PersonalDataBar } from "@/features/agents/PersonalDataBar"
import { ReplayView } from "@/features/agents/ReplayPage"
import { auditLine } from "@/features/agents/labels"
import type { Provenance } from "@/features/agents/types"

const CHANNEL: Record<string, string> = { email: "Email", whatsapp: "WhatsApp" }

/** The answers someone tracing a message wants first, read from the run (R7). */
function AtAGlance({ p }: { p: Provenance }) {
  const run = p.run
  if (!run) {
    return <p className="text-[12.5px] text-ink-700">Written by <Actor actor={p.message.created_by} />, not by an agent.</p>
  }
  const proposed = run.steps.find((s) => s.kind === "action.proposed")
  const d = proposed?.data ?? {}
  const inputs = Array.isArray(d.inputs) ? d.inputs.length : 0
  const models = run.steps.filter((s) => s.kind === "model.called").length
  const decision = run.decisions[0]
  const outcome = String(d.outcome ?? "")
  const reasons = Array.isArray(d.reasons) ? (d.reasons as string[]) : []
  return (
    <dl className="grid grid-cols-[10rem_1fr] gap-x-3 gap-y-1.5 text-[12.5px]">
      <dt className="text-ink-500">Written by</dt>
      <dd>{run.run.agent_name}{run.run.agent_version !== null && <span className="font-mono text-ink-300"> v{run.run.agent_version}</span>}</dd>
      <dt className="text-ink-500">What it was given</dt>
      <dd>{inputs ? `${inputs} record${inputs === 1 ? "" : "s"} or passage${inputs === 1 ? "" : "s"}, listed under the step below` : "Only what the person asked for"}</dd>
      <dt className="text-ink-500">Model calls</dt>
      <dd>{models ? `${models}, with the exact prompt and answer below` : "None: this message was not written by a model"}</dd>
      <dt className="text-ink-500">Rules check</dt>
      <dd>{outcome === "act" ? "Allowed to act alone" : outcome === "ask_person" ? `Had to ask a person: ${reasons.join("; ")}` : outcome ? `Blocked: ${reasons.join("; ")}` : "Not recorded"}</dd>
      <dt className="text-ink-500">Person's decision</dt>
      <dd>
        {decision
          ? decision.decided_by
            ? <>{decision.status === "approved" ? "Approved" : decision.status === "rejected" ? "Rejected" : decision.status} by <Actor actor={decision.decided_by} />{decision.decided_at ? ` on ${formatDateTime(decision.decided_at)}` : ""}{decision.note ? ` (“${decision.note}”)` : ""}</>
            : "Still waiting"
          : "None needed"}
      </dd>
      <dt className="text-ink-500">Steps intact</dt>
      <dd className={run.integrity.ok ? "text-teal-600" : "text-red-500 font-semibold"}>
        {run.integrity.ok ? `Yes, all ${run.integrity.checked} match their fingerprints` : "No: see the warning below"}
      </dd>
    </dl>
  )
}

/** Everything behind one message, on one page (R7): the message, the agent run
 * that wrote it step by step, and who held its account or contact back from agents. */
export function MessageTracePage() {
  const { messageId } = useParams()
  const [revealed, setRevealed] = useState<Provenance | null>(null)
  const { data: hidden, isLoading, error } = useQuery({
    queryKey: ["audit", "messages", messageId],
    queryFn: async () => (await api.get<Provenance>(`/audit/messages/${messageId}`)).data,
  })
  if (isLoading) return <Loading />
  if (error || !hidden) return <ErrorText error={error} fallback="Could not load this message" />
  const p = revealed ?? hidden
  const m = p.message

  return (
    <div className="space-y-3">
      {m.account_id && (
        <Link to={`/accounts/${m.account_id}`} className="text-[12.5px] text-teal-600 hover:underline">
          ← {m.account_name ?? "The account"}
        </Link>
      )}
      <PersonalDataBar<Provenance> pd={hidden.personal_data} revealed={revealed !== null}
        revealUrl={`/audit/messages/${messageId}/reveal`} onRevealed={setRevealed} />

      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0 gap-4">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <Tag>{CHANNEL[m.channel] ?? m.channel}</Tag>
              <Tag variant={m.status === "sent" ? "green" : m.status === "failed" ? "red" : "neutral"}>
                {m.status === "draft" ? "Draft, not sent" : m.status}
              </Tag>
              {m.ai_generated && <Tag variant="teal">{m.disclosure ?? "Written by AI"}</Tag>}
            </div>
            <CardTitle>{m.subject ?? `${CHANNEL[m.channel] ?? m.channel} message`}</CardTitle>
            <p className="text-[12.5px] text-ink-500">
              To {m.erased_at && m.contact_id ? "a contact whose data was erased" : m.contact_name ?? "the account"}{m.account_name ? ` at ${m.account_name}` : ""} ·{" "}
              {m.sent_at ? `sent ${formatDateTime(m.sent_at)}` : `drafted ${formatDateTime(m.created_at)}`}
            </p>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="whitespace-pre-wrap text-[13px] text-ink-900 rounded-md bg-surface-2 p-3">{m.body}</p>
          {m.ai_generated && (
            <p className="text-[12px] text-ink-500">
              The customer sees “{m.disclosure}” with this message, so they know an AI assistant wrote it.
            </p>
          )}
          {m.status === "draft" && (
            <p className="text-[12px] text-ink-500">Never sent: Forge cannot send messages until email and WhatsApp arrive in Step 6.</p>
          )}
          {m.erased_at && <p className="text-[12px] text-ink-500">The person's details were erased on {formatDateTime(m.erased_at)}.</p>}
          <AtAGlance p={p} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Held back from agents</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-[12.5px]">
          {p.active_takeovers.map((t) => (
            <p key={t.id} className="rounded-md bg-gold-100 text-ink-700 p-2">
              <b>Taken over now by <Actor actor={t.taken_by} /></b> since {formatDateTime(t.started_at)}
              {t.reason && <> ({t.reason})</>}: agents will not act on this {t.record_type} until it is handed back.
            </p>
          ))}
          {p.takeovers.length === 0 ? (
            <p className="text-ink-300">No one has taken this account or contact over from agents.</p>
          ) : (
            <ul className="divide-y divide-line">
              {p.takeovers.map((r) => (
                <li key={r.seq} className="py-1.5 flex flex-wrap gap-3">
                  <span className="text-ink-300 w-40 shrink-0">{formatDateTime(r.created_at)}</span>
                  <span className="w-44 shrink-0"><Actor actor={r.actor} /></span>
                  <span className="flex-1">{auditLine(r)}{r.data.reason ? `: “${String(r.data.reason)}”` : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {p.run ? (
        <>
          <h2 className="text-[13px] font-semibold text-ink-700 pt-1">How it was written</h2>
          <ReplayView data={p.run} />
        </>
      ) : null}
    </div>
  )
}
