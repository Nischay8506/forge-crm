import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { useDebounced } from "@/lib/useDebounced"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { OUTCOME_LABELS, conditionProblem } from "./labels"
import type { Catalog, Condition, Rule } from "./types"

interface Draft {
  id?: string
  agent_key: string
  tool_key: string
  conditions: Condition[]
  outcome: Rule["outcome"]
  /** The roles that decide, in order (H1); "" = anyone who can decide. */
  steps: string[]
  /** Deadline in working hours (H4); "" = the company default. */
  sla_hours: string
  note: string
  enabled: boolean
}

const EMPTY: Draft = { agent_key: "", tool_key: "", conditions: [], outcome: "ask_person",
  steps: [""], sla_hours: "", note: "", enabled: true }

function body(d: Draft) {
  return {
    agent_key: d.agent_key || null, tool_key: d.tool_key || null,
    conditions: d.conditions.map((c) => ({ ...c, value: Array.isArray(c.value) || typeof c.value !== "string" || c.value === "" || Number.isNaN(Number(c.value)) ? c.value : Number(c.value) })),
    outcome: d.outcome, approver_role_id: null,
    approval_steps: d.outcome === "ask_person" ? d.steps.filter(Boolean) : [],
    sla_hours: d.outcome === "ask_person" && d.sla_hours.trim() ? d.sla_hours.trim() : null,
    note: d.note || null, enabled: d.enabled,
  }
}

/** H1: who decides, in order. Each step is a role; the card moves to the next step
 * only after the one before approves, and each step needs a different person. */
function ChainEditor({ roles, steps, onChange }: {
  roles: { id: string; name: string }[]; steps: string[]; onChange: (s: string[]) => void
}) {
  const setAt = (i: number, v: string) => onChange(steps.map((x, j) => (j === i ? v : x)))
  const used = new Set(steps.filter(Boolean))
  return (
    <Field label="Who decides, in order" group
      hint={steps.length > 1 ? "Each step is decided by a different person, and the person who asked never decides." : "Add a step when a second role must approve after the first."}>
      <ol className="space-y-1.5">
        {steps.map((rid, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className="w-5 text-[12px] text-ink-300 shrink-0">{i + 1}.</span>
            <Select aria-label={`Step ${i + 1}`} value={rid} onChange={(e) => setAt(i, e.target.value)}>
              {steps.length === 1 && <option value="">Anyone who can decide</option>}
              {steps.length > 1 && rid === "" && <option value="">Pick a role</option>}
              {roles.map((r) => (
                <option key={r.id} value={r.id} disabled={r.id !== rid && used.has(r.id)}>{r.name}</option>
              ))}
            </Select>
            {steps.length > 1 && (
              <button type="button" className="text-[12px] text-ink-500 hover:text-red-500 shrink-0"
                onClick={() => onChange(steps.filter((_, j) => j !== i))}>Remove</button>
            )}
          </li>
        ))}
      </ol>
      {steps.length < 5 && (
        <button type="button" className="text-[12.5px] text-teal-600 hover:underline"
          onClick={() => onChange([...steps, ""])}>+ Add a step</button>
      )}
    </Field>
  )
}

function RuleBuilder({ catalog, initial, onDone }: { catalog: Catalog; initial: Draft; onDone: () => void }) {
  const qc = useQueryClient()
  const [d, setD] = useState<Draft>(initial)
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }))
  const agent = catalog.agents.find((a) => a.key === d.agent_key)
  const tools = catalog.tools.filter((t) => !agent || agent.tools.includes(t.key))
  const tool = catalog.tools.find((t) => t.key === d.tool_key)
  // Only facts the chosen action provides; any fact when the rule covers every action.
  const facts = catalog.facts.filter((f) => !tool || tool.facts.some((tf) => tf.key === f.key))
  const factOf = (key: string) => catalog.facts.find((f) => f.key === key)
  const localProblems = d.conditions.map((c) => conditionProblem(c, factOf(c.fact))).filter(Boolean) as string[]
  if (d.outcome === "ask_person" && d.steps.length > 1 && d.steps.some((x) => !x)) localProblems.push("Pick a role for every step")
  if (d.outcome === "ask_person" && d.sla_hours.trim() && !(Number(d.sla_hours) > 0)) localProblems.push("The deadline is a number of working hours, such as 4 or 0.5")

  const debounced = useDebounced(useMemo(() => body(d), [d]), 300)
  const preview = useQuery({
    queryKey: ["policies", "preview", debounced],
    queryFn: async () => (await api.post<{ sentence: string | null; problems: string[] }>("/policies/preview", debounced)).data,
    enabled: localProblems.length === 0,
  })
  const save = useMutation({
    mutationFn: async () => d.id ? api.put(`/policies/${d.id}`, body(d)) : api.post("/policies", body(d)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["policies"] }); qc.invalidateQueries({ queryKey: ["agents"] }); onDone() },
  })

  const setCond = (i: number, patch: Partial<Condition>) =>
    set({ conditions: d.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) })
  const addCond = () => {
    const f = facts[0]
    if (f) set({ conditions: [...d.conditions, { fact: f.key, op: f.ops[0], value: f.type === "choice" ? f.choices[0] : "" }] })
  }
  const problems = [...localProblems, ...(preview.data?.problems ?? []), ...errorProblems(save.error)]

  return (
    <Card className="border-gold-500">
      <CardHeader><CardTitle>{d.id ? "Change rule" : "New rule"}</CardTitle></CardHeader>
      <CardContent>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="When this agent">
              <Select value={d.agent_key} onChange={(e) => set({ agent_key: e.target.value, tool_key: "", conditions: [] })}>
                <option value="">Any agent</option>
                {catalog.agents.map((a) => <option key={a.key} value={a.key}>{a.name}{a.test_only ? " (test)" : ""}</option>)}
              </Select>
            </Field>
            <Field label="Wants to">
              <Select value={d.tool_key} onChange={(e) => set({ tool_key: e.target.value, conditions: [] })}>
                <option value="">Do anything</option>
                {tools.map((t) => <option key={t.key} value={t.key}>{t.title}</option>)}
              </Select>
            </Field>
          </div>

          <div className="space-y-2">
            <span className="block text-[12px] font-semibold text-ink-700">And (all of these hold)</span>
            {d.conditions.length === 0 && <p className="text-[12px] text-ink-300">No conditions: the rule applies every time.</p>}
            {d.conditions.map((c, i) => {
              const f = factOf(c.fact)
              return (
                <div key={i} className="grid grid-cols-[1fr_140px_1fr_auto] gap-2 items-center">
                  <Select value={c.fact} onChange={(e) => {
                    const nf = factOf(e.target.value)!
                    setCond(i, { fact: nf.key, op: nf.ops[0], value: nf.type === "choice" ? nf.choices[0] : "" })
                  }}>
                    {facts.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                  </Select>
                  <Select value={c.op} onChange={(e) => {
                    const multi = e.target.value === "in" || e.target.value === "not_in"
                    const first = Array.isArray(c.value) ? c.value[0] ?? "" : c.value
                    setCond(i, { op: e.target.value, value: multi ? (first ? [String(first)] : []) : first })
                  }}>
                    {(f?.ops ?? []).map((o) => <option key={o} value={o}>{catalog.ops[o]}</option>)}
                  </Select>
                  {f?.type === "choice" ? (
                    Array.isArray(c.value) ? (
                      <div className="flex gap-3 text-[12.5px]">
                        {f.choices.map((ch) => (
                          <label key={ch} className="flex items-center gap-1">
                            <input type="checkbox" checked={(c.value as string[]).includes(ch)}
                              onChange={(e) => setCond(i, { value: e.target.checked ? [...(c.value as string[]), ch] : (c.value as string[]).filter((v) => v !== ch) })} />
                            {ch}
                          </label>
                        ))}
                      </div>
                    ) : (
                      <Select value={String(c.value)} onChange={(e) => setCond(i, { value: e.target.value })}>
                        {f.choices.map((ch) => <option key={ch} value={ch}>{ch}</option>)}
                      </Select>
                    )
                  ) : (
                    <Input type="number" step="any" placeholder={f?.type === "money" ? "₹" : ""} value={String(c.value)}
                      onChange={(e) => setCond(i, { value: e.target.value })} />
                  )}
                  <button type="button" className="text-[12px] text-red-500 hover:underline"
                    onClick={() => set({ conditions: d.conditions.filter((_, j) => j !== i) })}>Remove</button>
                </div>
              )
            })}
            {facts.length > 0 && <Button type="button" size="sm" variant="ghost" onClick={addCond}>+ Add condition</Button>}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Then">
              <div className="flex gap-2">
                {(Object.keys(OUTCOME_LABELS) as Rule["outcome"][]).map((o) => (
                  <button key={o} type="button" onClick={() => set({ outcome: o })}
                    className={cn("rounded-md border px-3 py-2 text-[12.5px] font-medium",
                      d.outcome === o ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500")}>
                    {OUTCOME_LABELS[o].label}
                  </button>
                ))}
              </div>
            </Field>
            {d.outcome === "ask_person" && (
              <ChainEditor roles={catalog.roles} steps={d.steps} onChange={(steps) => set({ steps })} />
            )}
            {d.outcome === "ask_person" && (
              <Field label="Deadline for each step" hint="In working hours, counted on the company's working calendar. Leave empty for the company default.">
                <Input className="max-w-[140px]" inputMode="decimal" placeholder="Default" value={d.sla_hours}
                  onChange={(e) => set({ sla_hours: e.target.value })} />
              </Field>
            )}
          </div>
          <Field label="Note (optional)"><Input value={d.note} onChange={(e) => set({ note: e.target.value })} /></Field>

          <div className="rounded-md bg-surface-2 p-3 text-[13px]">
            <span className="text-ink-500">Reads as: </span>
            <span className="text-ink-900 font-medium">{preview.data?.sentence ?? "…"}</span>
          </div>
          {problems.length > 0 && (
            <ul className="bg-red-100 text-red-500 text-[12.5px] p-2 rounded-md space-y-0.5">
              {[...new Set(problems)].map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          <ErrorText error={problems.length ? null : save.error} fallback="Could not save the rule" />
          <div className="flex gap-2">
            <Button type="submit" disabled={save.isPending || localProblems.length > 0 || (preview.data?.problems.length ?? 0) > 0}>
              {save.isPending ? "Saving…" : "Save rule"}
            </Button>
            <Button type="button" variant="secondary" onClick={onDone}>Cancel</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

export function PoliciesPage() {
  const canManage = hasPermission(usePermissions(), "agents.manage")
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Draft | null>(null)
  const catalog = useQuery({
    queryKey: ["policies", "catalog"],
    queryFn: async () => (await api.get<Catalog>("/policies/catalog")).data,
  })
  const rules = useQuery({
    queryKey: ["policies", "list"],
    queryFn: async () => (await api.get<Rule[]>("/policies")).data,
  })
  const refresh = () => { qc.invalidateQueries({ queryKey: ["policies"] }); qc.invalidateQueries({ queryKey: ["agents"] }) }
  const toggle = useMutation({
    mutationFn: async (r: Rule) => api.put(`/policies/${r.id}`, { ...r, enabled: !r.enabled }),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: async (r: Rule) => api.delete(`/policies/${r.id}`),
    onSuccess: refresh,
  })

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <p className="text-[12.5px] text-ink-500 max-w-3xl">
          Checked before every action an agent takes. When several rules match, Block wins over Ask a person,
          which wins over Let it act. Every change is kept in the audit log.
        </p>
        {canManage && !editing && <Button onClick={() => setEditing(EMPTY)}>New rule</Button>}
      </div>
      {editing && catalog.data && (
        <RuleBuilder key={editing.id ?? "new"} catalog={catalog.data} initial={editing} onDone={() => setEditing(null)} />
      )}
      {(rules.isLoading || catalog.isLoading) && <Loading />}
      <ErrorText error={rules.error ?? catalog.error ?? toggle.error ?? remove.error} fallback="Could not load policy rules" />
      {rules.data && (
        <Card>
          {rules.data.length === 0 ? <Empty>No rules yet. Agents act up to their autonomy level.</Empty> : (
            <ul className="divide-y divide-line">
              {rules.data.map((r) => (
                <li key={r.id} className={cn("flex items-start gap-3 p-3", !r.enabled && "opacity-60")}>
                  <Tag variant={OUTCOME_LABELS[r.outcome].tone} className="mt-0.5 shrink-0">{OUTCOME_LABELS[r.outcome].label}</Tag>
                  <div className="flex-1 text-[13px]">
                    <div className="text-ink-900">{r.sentence}</div>
                    <div className="text-[11.5px] text-ink-300">
                      {r.note && <>{r.note} · </>}Last changed by <Actor actor={r.updated_by} />{!r.enabled && " · Off"}
                    </div>
                  </div>
                  {canManage && (
                    <div className="flex gap-3 text-[12px] shrink-0">
                      <button className="text-teal-600 hover:underline" onClick={() => setEditing({
                        id: r.id, agent_key: r.agent_key ?? "", tool_key: r.tool_key ?? "", conditions: r.conditions,
                        outcome: r.outcome, steps: r.approval_steps.length ? r.approval_steps : [r.approver_role_id ?? ""],
                        sla_hours: r.sla_hours ?? "", note: r.note ?? "", enabled: r.enabled })}>Change</button>
                      <button className="text-ink-500 hover:underline" onClick={() => toggle.mutate(r)}>{r.enabled ? "Turn off" : "Turn on"}</button>
                      <button className="text-red-500 hover:underline" onClick={() => { if (confirm("Delete this rule?")) remove.mutate(r) }}>Delete</button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  )
}
