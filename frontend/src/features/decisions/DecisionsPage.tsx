import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { AgentBadge } from "@/components/ui/AgentBadge"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Input } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDateTime } from "@/lib/utils"
import { Actor } from "@/features/steward/Actor"
import { STATUS_TONES } from "@/features/agents/labels"
import { hasPermission } from "@/lib/permissions"
import { usePermissions } from "@/stores/authStore"
import type { DecisionCard } from "@/features/agents/types"

type View = "waiting" | "notices" | "done"
const VIEWS: { key: View; label: string }[] = [
  { key: "waiting", label: "Waiting for you" },
  { key: "notices", label: "Notices" },
  { key: "done", label: "Done" },
]

export function useDecisionCount(enabled: boolean) {
  return useQuery({
    queryKey: ["decisions", "count"],
    queryFn: async () => (await api.get<{ waiting: number; notices: number }>("/decisions/count")).data,
    enabled,
    refetchInterval: 30_000,
  })
}

/** Where the person can look at the record the decision is about. */
function recordLink(d: DecisionCard): { to: string; label: string } | null {
  const cand = d.args.candidate_id
  if (d.tool_key === "steward.merge_records" && typeof cand === "string" && d.status === "pending") {
    return { to: `/steward/review/${cand}`, label: "Compare the two records" }
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
      <span className="text-ink-900 text-right">{children}</span>
    </div>
  )
}

function DecisionCardView({ d, listKey }: { d: DecisionCard; listKey: unknown[] }) {
  const qc = useQueryClient()
  const [note, setNote] = useState("")
  const [showChecks, setShowChecks] = useState(false)
  const act = useMutation({
    mutationFn: async (what: "approve" | "reject" | "acknowledge") =>
      (await api.post<DecisionCard>(`/decisions/${d.id}/${what}`, what === "acknowledge" ? undefined : { note: note || null })).data,
    onSuccess: (updated) => {
      // Keep the decided card where it is (showing the outcome) until the list is reloaded.
      qc.setQueryData<DecisionCard[]>(listKey, (list) => list?.map((x) => (x.id === updated.id ? updated : x)))
      qc.invalidateQueries({ queryKey: ["decisions", "count"] })
      qc.invalidateQueries({ queryKey: ["decisions", "done"] })
      qc.invalidateQueries({ queryKey: ["candidates"] })
      qc.invalidateQueries({ queryKey: ["accounts"] })
      qc.invalidateQueries({ queryKey: ["integrations"] })
    },
  })
  const link = recordLink(d)
  const canAudit = hasPermission(usePermissions(), "audit.read")
  const approval = d.kind === "approval"
  const pending = d.status === "pending"
  const res = d.result
  const style = KIND_STYLE[d.kind] ?? KIND_STYLE.notice

  return (
    <Card className={cn("p-4 space-y-2 border-t-4", style.border)}>
      <div className="flex items-center justify-between gap-2">
        <Tag variant={style.tag}>{d.tool_title}</Tag>
        <span className="text-[11px] text-ink-300">{formatDateTime(d.created_at)}</span>
      </div>
      <h4 className="text-[14px] font-semibold text-ink-900 leading-snug">{d.title}</h4>
      <div className="flex items-center gap-2 text-[11.5px] text-ink-500">
        <AgentBadge label={d.agent_name.toUpperCase()} />
        {d.approver_role && <span>· {d.approver_role.name} decides</span>}
      </div>
      <div>
        {d.facts.map((f) => <Row key={f.key} k={f.label}>{f.value}</Row>)}
        {d.reasons.length > 0 && (d.kind === "alert" || d.kind === "task" ? (
          <ul className="text-[12.5px] text-ink-700 space-y-1 py-1">
            {d.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        ) : (
          <Row k={approval ? "Why it is asking" : "Why"}>{d.reasons.join("; ")}</Row>
        ))}
      </div>
      {d.recommendation && (
        <p className="text-[12.5px] bg-teal-100 text-teal-600 rounded-md p-2">
          <span className="font-semibold">Agent recommends: </span>{d.recommendation}
        </p>
      )}
      <div className="flex items-center justify-between text-[12px]">
        <span className="flex gap-3">
          {link && <Link to={link.to} className="text-teal-600 hover:underline">{link.label}</Link>}
          {d.run_id && canAudit && <Link to={`/agents/runs/${d.run_id}`} className="text-teal-600 hover:underline">How this came about</Link>}
        </span>
        {d.checks.length > 0 && (
          <button onClick={() => setShowChecks((s) => !s)} className="text-ink-500 hover:text-ink-900">
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
            <div className="flex gap-2">
              <Button variant="gold" size="sm" disabled={act.isPending} onClick={() => act.mutate("approve")}>Approve</Button>
              <Button variant="danger" size="sm" disabled={act.isPending} onClick={() => act.mutate("reject")}>Reject</Button>
            </div>
          </div>
        ) : (
          <p className="text-[12px] text-ink-300">Waiting for {d.approver_role?.name ?? "someone who can decide"}.</p>
        )
      )}
      {d.kind === "notice" && d.status === "open" && (
        <Button variant="secondary" size="sm" disabled={act.isPending} onClick={() => act.mutate("acknowledge")}>Mark as read</Button>
      )}
      {d.kind === "alert" && d.status === "open" && (
        <div className="space-y-1">
          <Button variant="secondary" size="sm" disabled={act.isPending} onClick={() => act.mutate("acknowledge")}>I'm on it</Button>
          <p className="text-[11.5px] text-ink-300">The alert closes on its own once the connection works again.</p>
        </div>
      )}
      {d.kind === "task" && d.status === "open" && (
        <p className="text-[11.5px] text-ink-300">Closes on its own once every record is dealt with.</p>
      )}
      {act.isPending && <p className="text-[12px] text-ink-500">Working…</p>}
      <ErrorText error={act.error} fallback="Could not save the decision" />
      {approval && !pending && (
        <div className="text-[12px] space-y-1 pt-1">
          <div className="flex items-center gap-2">
            <Tag variant={STATUS_TONES[d.status]}>{d.status}</Tag>
            <Actor actor={d.decided_by} />
            <span className="text-ink-300">{formatDateTime(d.decided_at)}</span>
          </div>
          {d.note && <p className="text-ink-500">“{d.note}”</p>}
          {res && res.status !== "done" && (
            <p className="text-red-500">Not done: {res.error ?? res.reasons.join("; ")}</p>
          )}
        </div>
      )}
      {d.kind === "notice" && d.status === "acknowledged" && <Tag variant="neutral">Read</Tag>}
      {(d.kind === "alert" || d.kind === "task") && d.status !== "open" && (
        <div className="text-[12px] flex items-center gap-2">
          <Tag variant={STATUS_TONES[d.status] ?? "neutral"}>{d.status === "acknowledged" ? "someone is on it" : d.status}</Tag>
          {d.decided_by && <Actor actor={d.decided_by} />}
          <span className="text-ink-300">{formatDateTime(d.decided_at)}</span>
          {d.note && <span className="text-ink-500">{d.note}</span>}
        </div>
      )}
    </Card>
  )
}

export function DecisionsPage() {
  const [view, setView] = useState<View>("waiting")
  const count = useDecisionCount(true)
  const { data, isLoading, error } = useQuery({
    queryKey: ["decisions", view],
    queryFn: async () => (await api.get<DecisionCard[]>("/decisions", { params: { view, limit: 100 } })).data,
  })
  const badge = (v: View) => v === "waiting" ? count.data?.waiting : v === "notices" ? count.data?.notices : undefined

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between border-b border-line">
        <div className="flex items-center gap-3 pb-3">
          <h1 className="text-2xl font-bold text-ink-900">Decisions</h1>
          {!!count.data?.waiting && <Tag variant="gold">{count.data.waiting} waiting</Tag>}
        </div>
        <nav className="flex gap-1">
          {VIEWS.map((v) => (
            <button key={v.key} onClick={() => setView(v.key)}
              className={cn("px-3 py-2 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900",
                view === v.key && "text-ink-900 border-teal-500")}>
              {v.label}{badge(v.key) ? ` (${badge(v.key)})` : ""}
            </button>
          ))}
        </nav>
      </div>
      <p className="text-[12.5px] text-ink-500">
        {view === "waiting" && "Actions agents want to take but need a person for, and integration alerts and tasks. Approving runs the action as proposed; the agent's stops and block rules are checked again first."}
        {view === "notices" && "What agents did on their own at level L1, and what policy rules stopped."}
        {view === "done" && "Decided, superseded and read items, newest first."}
      </p>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load decisions" />
      {data && data.length === 0 && <Card><Empty>Nothing here.</Empty></Card>}
      {data && data.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
          {data.map((d) => <DecisionCardView key={d.id} d={d} listKey={["decisions", view]} />)}
        </div>
      )}
    </div>
  )
}
