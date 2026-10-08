import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { AccountPicker } from "@/features/accounts/AccountPicker"
import { Actor } from "@/features/steward/Actor"
import type { AccountSummary } from "@/features/steward/types"
import { LEVEL_HELP, OUTCOME_LABELS, auditLine, lowerFirst } from "./labels"
import type { AgentDetail, TryResult } from "./types"

const TIERS = ["strategic", "key", "standard"]

function LevelPicker({ agent, canManage }: { agent: AgentDetail; canManage: boolean }) {
  const qc = useQueryClient()
  const set = useMutation({
    mutationFn: async (level: number) => api.put(`/agents/${agent.key}/level`, { level }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  })
  const levels = Array.from({ length: agent.max_level + 1 }, (_, i) => i)
  return (
    <div className="space-y-2">
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${levels.length}, minmax(0, 1fr))` }}>
        {levels.map((l) => (
          <button key={l} disabled={!canManage || set.isPending || l === agent.level}
            onClick={() => set.mutate(l)}
            className={cn("rounded-md border p-2 text-left text-[12px] disabled:cursor-default",
              l === agent.level ? "border-teal-500 bg-teal-100" : "border-line bg-surface-1 hover:bg-surface-0",
              !canManage && l !== agent.level && "opacity-60")}>
            <div className="font-mono font-semibold text-ink-900">L{l}</div>
            <div className="text-ink-500">{LEVEL_HELP[l]}</div>
          </button>
        ))}
      </div>
      <ErrorText error={set.error} fallback="Could not change the level" />
    </div>
  )
}

function Overrides({ agent, canManage }: { agent: AgentDetail; canManage: boolean }) {
  const qc = useQueryClient()
  const [scope, setScope] = useState<"account_tier" | "account">("account_tier")
  const [tier, setTier] = useState("strategic")
  const [account, setAccount] = useState<AccountSummary | null>(null)
  const [level, setLevel] = useState(0)
  const refresh = () => qc.invalidateQueries({ queryKey: ["agents"] })
  const add = useMutation({
    mutationFn: async () => api.post(`/agents/${agent.key}/overrides`, {
      scope, autonomy_level: level,
      account_tier: scope === "account_tier" ? tier : null,
      account_id: scope === "account" ? account?.id : null,
    }),
    onSuccess: () => { setAccount(null); refresh() },
  })
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/agents/overrides/${id}`),
    onSuccess: refresh,
  })
  return (
    <div className="space-y-3">
      {agent.overrides.length === 0 ? (
        <p className="text-[12.5px] text-ink-300">No overrides: L{agent.level} applies to every account.</p>
      ) : (
        <ul className="divide-y divide-line border border-line rounded-md">
          {agent.overrides.map((o) => (
            <li key={o.id} className="flex items-center gap-3 p-2 text-[13px]">
              <span className="flex-1">{o.target}</span>
              <Tag variant="teal">{o.code}</Tag>
              {canManage && (
                <button className="text-[12px] text-red-500 hover:underline" onClick={() => remove.mutate(o.id)}>Remove</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <form className="grid grid-cols-[140px_1fr_90px_auto] gap-2 items-end"
          onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
          <Field label="For">
            <Select value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
              <option value="account_tier">A tier</option>
              <option value="account">One account</option>
            </Select>
          </Field>
          {scope === "account_tier" ? (
            <Field label="Tier">
              <Select value={tier} onChange={(e) => setTier(e.target.value)}>
                {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
              </Select>
            </Field>
          ) : (
            <Field label="Account" group><AccountPicker value={account} onChange={setAccount} /></Field>
          )}
          <Field label="Level">
            <Select value={level} onChange={(e) => setLevel(Number(e.target.value))}>
              {Array.from({ length: agent.max_level + 1 }, (_, i) => <option key={i} value={i}>L{i}</option>)}
            </Select>
          </Field>
          <Button type="submit" variant="secondary" disabled={add.isPending || (scope === "account" && !account)}>Add</Button>
        </form>
      )}
      <ErrorText error={add.error ?? remove.error} fallback="Could not save the override" />
    </div>
  )
}

function StopPanel({ agent, canManage }: { agent: AgentDetail; canManage: boolean }) {
  const qc = useQueryClient()
  const [reason, setReason] = useState("")
  const refresh = () => { qc.invalidateQueries({ queryKey: ["agents"] }); qc.invalidateQueries({ queryKey: ["stops"] }) }
  const stop = useMutation({
    mutationFn: async () => api.post("/kill-switches", { scope: "agent", agent_key: agent.key, reason }),
    onSuccess: () => { setReason(""); refresh() },
  })
  const release = useMutation({
    mutationFn: async (id: string) => api.post(`/kill-switches/${id}/release`),
    onSuccess: refresh,
  })
  return (
    <div className="space-y-2">
      {agent.stops.length === 0 ? (
        <p className="text-[12.5px] text-ink-500">Running. Nothing is stopping {agent.name}.</p>
      ) : agent.stops.map((s) => (
        <div key={s.id} className="rounded-md bg-red-100 p-2 text-[12.5px] flex items-start justify-between gap-2">
          <div>
            <div className="font-semibold text-red-500">Stopped: {s.target}</div>
            <div className="text-ink-700">{s.reason}</div>
            <div className="text-ink-500 text-[11.5px]"><Actor actor={s.created_by} /> · {formatDateTime(s.created_at)}</div>
          </div>
          {canManage && <Button size="sm" variant="secondary" onClick={() => release.mutate(s.id)}>Let it run</Button>}
        </div>
      ))}
      {canManage && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); stop.mutate() }}>
          <Input placeholder="Why stop it?" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button type="submit" variant="danger" disabled={!reason.trim() || stop.isPending}>Stop {agent.name}</Button>
        </form>
      )}
      <ErrorText error={stop.error ?? release.error} fallback="Could not change the stop" />
    </div>
  )
}

function TryIt() {
  const qc = useQueryClient()
  const [amount, setAmount] = useState("150000")
  const [account, setAccount] = useState<AccountSummary | null>(null)
  const run = useMutation({
    mutationFn: async () => (await api.post<TryResult>("/agents/sandbox/try", {
      amount, account_id: account?.id ?? null })).data,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["decisions"] }); qc.invalidateQueries({ queryKey: ["agents"] }) },
  })
  const r = run.data
  const tone = { done: "green", waiting: "gold", blocked: "red", failed: "red" } as const
  const words = { done: "Done: it acted on its own", waiting: "Waiting for a person", blocked: "Blocked", failed: "Failed" }
  return (
    <div className="space-y-3">
      <p className="text-[12.5px] text-ink-500">
        Ask the Sandbox Agent to record a pretend payment and see what your rules do with it. Nothing real changes.
      </p>
      <form className="grid grid-cols-[160px_1fr_auto] gap-2 items-end" onSubmit={(e) => { e.preventDefault(); run.mutate() }}>
        <Field label="Amount (₹)"><Input type="number" min="0.01" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <Field label="Account (optional)" group><AccountPicker value={account} onChange={setAccount} /></Field>
        <Button type="submit" disabled={run.isPending || !amount}>Try it</Button>
      </form>
      <ErrorText error={run.error} fallback="Could not run the test" />
      {r && (
        <div className="rounded-md border border-line p-3 text-[13px] space-y-1">
          <div className="flex items-center gap-2"><Tag variant={tone[r.status]}>{words[r.status]}</Tag><span>{r.summary}</span></div>
          {r.reasons.map((x) => <p key={x} className="text-ink-500 text-[12.5px]">{x}</p>)}
          {r.status === "waiting" && <Link to="/decisions" className="text-teal-600 text-[12.5px] hover:underline">Open Decisions</Link>}
        </div>
      )}
    </div>
  )
}

export function AgentDetailPage() {
  const { key } = useParams()
  const perms = usePermissions()
  const canManage = hasPermission(perms, "agents.manage")
  const canAudit = hasPermission(perms, "audit.read")
  const { data: a, isLoading, error } = useQuery({
    queryKey: ["agents", key],
    queryFn: async () => (await api.get<AgentDetail>(`/agents/${key}`)).data,
  })
  if (isLoading) return <Loading />
  if (error || !a) return <ErrorText error={error} fallback="Could not load the agent" />

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-bold text-ink-900">{a.name}</h2>
          <Tag variant="teal">{a.code}</Tag>
          {a.test_only && <Tag>Test only</Tag>}
          {a.stopped && <Tag variant="red">Stopped</Tag>}
        </div>
        <p className="text-[13px] text-ink-500 max-w-3xl">{a.purpose}</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        <Card>
          <CardHeader><CardTitle>What it can do</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-[13px]">
            <ul className="space-y-2">
              {a.tools.map((t) => (
                <li key={t.key} className="rounded-md bg-surface-2 p-2">
                  <div className="font-semibold text-ink-900">{t.title} <span className="font-mono text-[10.5px] text-ink-300">{t.key} v{t.version}</span></div>
                  <div className="text-ink-500">{t.description}</div>
                  {t.facts.length > 0 && (
                    <div className="text-[12px] text-ink-300 mt-1">Rules can check: {t.facts.map((f) => lowerFirst(f.label)).join(", ")}</div>
                  )}
                </li>
              ))}
            </ul>
            <div className="text-[12.5px] text-ink-500 space-y-0.5">
              <div><b className="text-ink-700">Starts when:</b> {a.triggers.join("; ") || "asked"}</div>
              <div><b className="text-ink-700">AI model:</b> {a.model_tier ?? "none, it follows fixed rules"}</div>
              <div><b className="text-ink-700">Acts as:</b> its own service identity (<span className="font-mono">{a.actor}</span>), never a person's login</div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Rules that apply</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {a.rules.length === 0 ? (
              <p className="text-[12.5px] text-ink-500">No rules. Within its level it may act on anything in its charter.</p>
            ) : (
              <ul className="space-y-2">
                {a.rules.map((r) => (
                  <li key={r.id} className={cn("flex items-start gap-2 text-[13px]", !r.enabled && "opacity-50")}>
                    <Tag variant={OUTCOME_LABELS[r.outcome].tone} className="shrink-0 mt-0.5">{OUTCOME_LABELS[r.outcome].label}</Tag>
                    <span>{r.sentence}{!r.enabled && " (off)"}</span>
                  </li>
                ))}
              </ul>
            )}
            <Link to="/agents/policies" className="text-[12.5px] text-teal-600 hover:underline">Edit policy rules</Link>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>How much it does on its own</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <LevelPicker agent={a} canManage={canManage} />
            <div className="space-y-2">
              <h4 className="text-[13px] font-semibold text-ink-700">Different level for some accounts</h4>
              <p className="text-[12px] text-ink-500">A named account wins over its tier. When an action touches several accounts the most careful level applies.</p>
              <Overrides agent={a} canManage={canManage} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Stop</CardTitle></CardHeader>
          <CardContent><StopPanel agent={a} canManage={canManage} /></CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Activity</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-4 gap-2 text-center">
              {([["Done", a.activity.done, "text-green-500"], ["Asked a person", a.activity.asked, "text-gold-500"],
                ["Blocked", a.activity.blocked, "text-red-500"], ["Failed", a.activity.failed, "text-red-500"]] as const).map(([l, n, c]) => (
                <div key={l} className="rounded-md bg-surface-2 p-2">
                  <div className={cn("text-xl font-bold font-disp", c)}>{n}</div>
                  <div className="text-[11px] text-ink-500">{l}</div>
                </div>
              ))}
            </div>
            {a.recent.length === 0 ? <Empty>Nothing yet.</Empty> : (
              <ul className="text-[12.5px] divide-y divide-line">
                {a.recent.slice(0, 8).map((r) => (
                  <li key={r.seq} className="py-1.5 flex gap-2">
                    <span className="text-ink-300 w-40 shrink-0">{formatDateTime(r.created_at)}</span>
                    <span className="text-ink-700">{auditLine(r)}</span>
                  </li>
                ))}
              </ul>
            )}
            {canAudit && <Link to={`/agents/audit?agent=${a.key}`} className="text-[12.5px] text-teal-600 hover:underline">Full audit log</Link>}
          </CardContent>
        </Card>

        {a.key === "sandbox" && canManage && (
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle>Try it</CardTitle></CardHeader>
            <CardContent><TryIt /></CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
