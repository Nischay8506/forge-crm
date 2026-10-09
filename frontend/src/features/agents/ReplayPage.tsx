import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/config/api"
import { AgentBadge } from "@/components/ui/AgentBadge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDateTime } from "@/lib/utils"
import { Actor } from "@/features/steward/Actor"
import { OUTCOME_LABELS, RUN_STATUS, STATUS_TONES, auditLine, offset, shortHash, triggerLabel } from "./labels"
import { PersonalDataBar } from "./PersonalDataBar"
import { RunTally } from "./RunsPage"
import type { AuditRow, Check, Input, ModelCall, Replay, ReplayDecision } from "./types"

type Item = { at: string; step?: AuditRow; call?: ModelCall }

const RECORD_LINKS: Record<string, (id: string) => string> = {
  account: (id) => `/accounts/${id}`,
  erp_connection: (id) => `/integrations/connections/${id}`,
}

const VERDICT: Record<string, string> = { act: "allowed", ask_person: "needs a person", block: "blocked" }

const STEP_TONE: Record<string, string> = {
  "action.executed": "bg-green-500", "decision.approved": "bg-green-500",
  "decision.requested": "bg-gold-500", "action.blocked": "bg-red-500", "action.failed": "bg-red-500",
  "decision.rejected": "bg-red-500", "model.failed": "bg-red-500", "contact.erased": "bg-ink-500",
}

function inputLabel(i: Input, names: Map<string, string | null>): string {
  if (i.source === "record") {
    const name = i.record_id ? names.get(i.record_id) : null
    return `The ${i.record_type ?? "record"}${name ? ` ${name}` : ""}, as it was then`
  }
  return i.source.charAt(0).toUpperCase() + i.source.slice(1)
}

/** What the agent was given before this step (R2). */
function Inputs({ inputs, names }: { inputs: Input[]; names: Map<string, string | null> }) {
  if (inputs.length === 0) return null
  return (
    <details>
      <summary className="text-[12px] text-ink-500 cursor-pointer">What the agent was given ({inputs.length})</summary>
      <ul className="space-y-1.5 mt-1.5">
        {inputs.map((i, n) => (
          <li key={n} className="space-y-0.5">
            <p className="text-[12px] text-ink-700">
              {inputLabel(i, names)} <span className="text-ink-300">· read {formatDateTime(i.read_at)}</span>
            </p>
            {typeof i.content === "string"
              ? <p className="bg-surface-2 rounded-md p-2 text-[12px] whitespace-pre-wrap">{i.content}</p>
              : <Json value={i.content} />}
          </li>
        ))}
      </ul>
    </details>
  )
}

/** Where a step's details stand: removed by retention or erasure, or missing. */
function EvidenceNote({ step }: { step: AuditRow }) {
  const e = step.evidence
  if (e.state === "removed") {
    return (
      <p className="text-[12px] text-ink-500 italic">
        Details removed {e.removed_at ? `on ${formatDateTime(e.removed_at)}` : ""} ({e.reason}). What happened, and
        the fingerprint of what was there, stay in the chain.
      </p>
    )
  }
  if (e.state === "missing") return <p className="text-[12px] text-red-500 font-semibold">This step's details are missing.</p>
  if (e.state === "unreadable") return <p className="text-[12px] text-red-500">These details cannot be read: {e.reason}</p>
  return null
}

function Json({ value }: { value: unknown }) {
  return <pre className="bg-surface-2 rounded-md p-2 text-[11px] overflow-x-auto max-h-72">{JSON.stringify(value, null, 2)}</pre>
}

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="text-[12px] space-y-0.5">
      {checks.map((c) => (
        <li key={c.check} className="flex gap-2">
          <span className={c.ok ? "text-green-500" : "text-red-500"}>{c.ok ? "✓" : "✕"}</span>
          <span><b className="font-semibold">{c.check.replace(/_/g, " ")}</b>: {c.detail}</span>
        </li>
      ))}
    </ul>
  )
}

function DecisionBox({ d }: { d: ReplayDecision }) {
  return (
    <div className="rounded-md border border-line p-2.5 space-y-1 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <Tag variant={STATUS_TONES[d.status] ?? "neutral"}>{d.status}</Tag>
        <span className="font-semibold">{d.title}</span>
      </div>
      {d.reasons.length > 0 && <p className="text-ink-500">Why it asked: {d.reasons.join("; ")}</p>}
      {d.recommendation && <p className="text-ink-500">Agent recommended: {d.recommendation}</p>}
      {d.decided_by && (
        <p className="flex flex-wrap items-center gap-1.5 text-ink-500">
          Decided by <Actor actor={d.decided_by} /> on {formatDateTime(d.decided_at)}
        </p>
      )}
      {d.note && <p className="text-ink-500">Their note: “{d.note}”</p>}
    </div>
  )
}

/** What one step carried, laid out for its kind; everything else as raw data. */
function StepDetail({ step, decisions, names }: {
  step: AuditRow; decisions: ReplayDecision[]; names: Map<string, string | null>
}) {
  const d = step.data
  if (step.kind === "model.called" || step.kind === "model.failed") {
    const msgs = (d.messages ?? []) as { role: string; content: string }[]
    return (
      <div className="space-y-2 text-[12.5px]">
        <p>
          {d.model ? <>Model <span className="font-mono">{String(d.model)}</span> ({String(d.provider)}) · </> : null}
          tier {String(d.tier)} · task <span className="font-mono">{String(d.task)}</span>
          {d.agent_version ? <> · agent v{String(d.agent_version)}</> : null}
        </p>
        <Inputs inputs={(d.inputs ?? []) as Input[]} names={names} />
        {msgs.length > 0 && (
          <div className="space-y-1">
            <p className="text-[12px] text-ink-500">The prompt, exactly as sent</p>
            {msgs.map((m, i) => (
              <div key={i} className="bg-surface-2 rounded-md p-2">
                <span className="text-[10.5px] uppercase tracking-wide text-ink-300">{m.role}</span>
                <p className="whitespace-pre-wrap text-[12px]">{m.content}</p>
              </div>
            ))}
          </div>
        )}
        {d.answer !== undefined && (
          <div>
            <p className="text-[12px] text-ink-500">The model's answer</p>
            <p className="bg-teal-100 rounded-md p-2 whitespace-pre-wrap text-[12px]">{String(d.answer)}</p>
          </div>
        )}
        {d.error ? <p className="text-red-500">{String(d.error)}</p> : null}
      </div>
    )
  }
  if (step.kind === "action.proposed") {
    const facts = Object.entries((d.facts ?? {}) as Record<string, unknown>)
    const out = OUTCOME_LABELS[String(d.outcome)]
    return (
      <div className="space-y-2">
        <p className="text-[12.5px]">
          Tool <span className="font-mono">{String(d.tool)}</span> v{String(d.version)} · rules said{" "}
          <b className={cn(d.outcome === "block" ? "text-red-500" : d.outcome === "ask_person" ? "text-gold-500" : "text-green-500")}>
            {VERDICT[String(d.outcome)] ?? out?.label ?? String(d.outcome)}
          </b>{" "}at L{String(d.level)}
          {d.approved_by ? <> · approved by <Actor actor={String(d.approved_by)} /></> : null}
        </p>
        {Array.isArray(d.checks) && d.checks.length > 0 && <Checks checks={d.checks as Check[]} />}
        {facts.length > 0 && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[12px]">
            {facts.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-ink-500">{k.replace(/_/g, " ")}</dt>
                <dd className="text-ink-900">{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
              </div>
            ))}
          </dl>
        )}
        <Inputs inputs={(d.inputs ?? []) as Input[]} names={names} />
        {d.args !== undefined && (
          <details>
            <summary className="text-[12px] text-ink-500 cursor-pointer">What the agent asked to do</summary>
            <Json value={d.args} />
          </details>
        )}
      </div>
    )
  }
  if (step.kind === "decision.requested") {
    const card = decisions.find((x) => x.id === d.decision_id)
    return card ? <DecisionBox d={card} /> : <Json value={d} />
  }
  if (step.kind === "action.executed") return d.result !== undefined ? <Json value={d.result} /> : null
  if (step.kind === "action.failed") return <p className="text-[12.5px] text-red-500">{String(d.error)}</p>
  if (step.kind === "action.blocked") {
    return <ul className="text-[12.5px] text-red-500 list-disc pl-4">{((d.reasons ?? []) as string[]).map((r) => <li key={r}>{r}</li>)}</ul>
  }
  return <Json value={d} />
}

function StepItem({ item, start, decisions, broken, names }: {
  item: Item; start: string; decisions: ReplayDecision[]; broken: string | undefined
  names: Map<string, string | null>
}) {
  const [open, setOpen] = useState(item.step?.kind === "action.proposed" || item.step?.kind === "decision.requested")
  const s = item.step
  const c = item.call
  return (
    <li className="relative pl-6 pb-4 last:pb-0">
      <span className={cn("absolute -left-[5.5px] top-1.5 h-2.5 w-2.5 rounded-full",
        broken ? "bg-red-500 ring-2 ring-red-100" : s ? STEP_TONE[s.kind] ?? "bg-teal-500" : "bg-ink-300")} />
      <button onClick={() => setOpen((o) => !o)} className="w-full text-left flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="font-mono text-[11px] text-ink-300 w-16 shrink-0" title={formatDateTime(item.at)}>{offset(start, item.at)}</span>
        {s ? (
          <>
            <span className="text-[12px] w-44 shrink-0"><Actor actor={s.actor} /></span>
            <span className="text-[13px] text-ink-900 flex-1">{auditLine(s)}</span>
          </>
        ) : c && (
          <span className="text-[13px] text-ink-900 flex-1">
            Model call: {c.model} ({c.provider}) · {c.input_tokens.toLocaleString("en-IN")} in,{" "}
            {c.output_tokens.toLocaleString("en-IN")} out · {(c.latency_ms / 1000).toFixed(1)} s
            {c.status !== "ok" && <span className="text-red-500"> · {c.status}{c.error ? `: ${c.error}` : ""}</span>}
          </span>
        )}
      </button>
      {open && s && (
        <div className="mt-2 ml-[76px] space-y-1.5">
          <EvidenceNote step={s} />
          <StepDetail step={s} decisions={decisions} names={names} />
          <p className="font-mono text-[10.5px] text-ink-300">
            #{s.seq} · {s.kind} · fingerprint {shortHash(s.hash)}
          </p>
          {broken && <p className="text-[12px] text-red-500 font-semibold">{broken}</p>}
        </div>
      )}
    </li>
  )
}

export function ReplayPage() {
  const { runId } = useParams()
  const [revealed, setRevealed] = useState<Replay | null>(null)
  const { data: hidden, isLoading, error } = useQuery({
    queryKey: ["audit", "runs", runId],
    queryFn: async () => (await api.get<Replay>(`/audit/runs/${runId}`)).data,
  })
  if (isLoading) return <Loading />
  if (error || !hidden) return <ErrorText error={error} fallback="Could not load this run" />
  return (
    <div className="space-y-3">
      <Link to="/agents/runs" className="text-[12.5px] text-teal-600 hover:underline">← All runs</Link>
      <PersonalDataBar<Replay> pd={hidden.personal_data} revealed={revealed !== null}
        revealUrl={`/audit/runs/${runId}/reveal`} onRevealed={setRevealed} />
      <ReplayView data={revealed ?? hidden} />
    </div>
  )
}

/** A run step by step; also shown under a traced message. */
export function ReplayView({ data }: { data: Replay }) {
  const { run, integrity } = data
  const names = new Map(data.records.map((r) => [r.record_id, r.name]))
  const modelSteps = data.steps.filter((s) => s.kind === "model.called").length
  const st = RUN_STATUS[run.status] ?? { label: run.status, tone: "neutral" as const }
  const items: Item[] = [
    ...data.steps.map((step): Item => ({ at: step.created_at, step })),
    // Model calls the trail already shows with their prompt (R2) are not listed twice.
    ...data.model_calls.filter((c) => modelSteps === 0 || c.status !== "ok")
      .map((call): Item => ({ at: call.created_at, call })),
  ].sort((a, b) => a.at.localeCompare(b.at) || (a.step ? -1 : 1))
  const problems = new Map(integrity.problems.map((p) => [p.seq, p.problem]))

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0 gap-4">
          <div className="space-y-1">
            <span className="flex items-center gap-1.5">
              <AgentBadge label={run.agent_name.toUpperCase()} />
              {run.agent_version !== null && <span className="font-mono text-[11px] text-ink-300">v{run.agent_version}</span>}
            </span>
            <CardTitle>{triggerLabel(run)}</CardTitle>
            <p className="text-[12.5px] text-ink-500">
              Started {formatDateTime(run.started_at)}
              {run.finished_at && <> · finished {offset(run.started_at, run.finished_at).slice(1)} later</>}
            </p>
          </div>
          <Tag variant={st.tone}>{st.label}</Tag>
        </CardHeader>
        <CardContent className="space-y-2 text-[12.5px]">
          <RunTally run={run} />
          {data.records.length > 0 && (
            <p className="flex flex-wrap gap-x-2">
              <span className="text-ink-500">About:</span>
              {data.records.map((r) => {
                const label = r.name ?? r.record_type.replace(/_/g, " ")
                const to = RECORD_LINKS[r.record_type]?.(r.record_id)
                return to
                  ? <Link key={r.record_id} to={to} className="text-teal-600 hover:underline">{label}</Link>
                  : <span key={r.record_id}>{label}</span>
              })}
            </p>
          )}
          {integrity.ok ? (
            <p className="rounded-md bg-teal-100 text-teal-600 p-2">
              These {integrity.checked} steps are intact: each one still matches its fingerprint and points at the
              record written before it.
            </p>
          ) : (
            <div className="rounded-md bg-red-100 text-red-500 p-2 space-y-1" role="alert">
              <p className="font-semibold">Some steps were changed or removed after they were written.</p>
              <ul className="list-disc pl-4">{integrity.problems.map((p) => <li key={p.seq}>{p.problem}</li>)}</ul>
              <Link to="/agents/audit" className="underline">Check the whole chain on the Audit log</Link>
            </div>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Step by step</CardTitle></CardHeader>
        <CardContent>
          {items.length === 0 ? <p className="text-[12.5px] text-ink-300">This run recorded no steps.</p> : (
            <ol className="border-l border-line ml-1">
              {items.map((it) => (
                <StepItem key={it.step ? `s${it.step.seq}` : `c${it.call!.id}`} item={it} start={run.started_at}
                  decisions={data.decisions} broken={it.step ? problems.get(it.step.seq) : undefined} names={names} />
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
