import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { AccountPicker } from "@/features/accounts/AccountPicker"
import { Actor } from "@/features/steward/Actor"
import type { AccountSummary } from "@/features/steward/types"
import type { AgentSummary, Stop } from "./types"

function StopForm({ agents }: { agents: AgentSummary[] }) {
  const qc = useQueryClient()
  const [scope, setScope] = useState<Stop["scope"]>("tenant")
  const [agentKey, setAgentKey] = useState(agents[0]?.key ?? "")
  const [account, setAccount] = useState<AccountSummary | null>(null)
  const [reason, setReason] = useState("")
  const stop = useMutation({
    mutationFn: async () => api.post("/kill-switches", {
      scope, reason, agent_key: scope === "agent" ? agentKey : null,
      account_id: scope === "account" ? account?.id : null }),
    onSuccess: () => {
      setReason(""); setAccount(null)
      qc.invalidateQueries({ queryKey: ["stops"] }); qc.invalidateQueries({ queryKey: ["agents"] })
    },
  })
  return (
    <form className="grid grid-cols-[170px_1fr] gap-3" onSubmit={(e) => { e.preventDefault(); stop.mutate() }}>
      <Field label="Stop">
        <Select value={scope} onChange={(e) => setScope(e.target.value as Stop["scope"])}>
          <option value="tenant">All agents</option>
          <option value="agent">One agent</option>
          <option value="account">All agents on one account</option>
        </Select>
      </Field>
      {scope === "agent" ? (
        <Field label="Agent">
          <Select value={agentKey} onChange={(e) => setAgentKey(e.target.value)}>
            {agents.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
          </Select>
        </Field>
      ) : scope === "account" ? (
        <Field label="Account" group><AccountPicker value={account} onChange={setAccount} /></Field>
      ) : <div />}
      <Field label="Why" className="col-span-2">
        <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. wrong merges reported by the Pune team" />
      </Field>
      <div className="col-span-2 flex items-center gap-3">
        <Button type="submit" variant="danger" disabled={!reason.trim() || stop.isPending || (scope === "account" && !account)}>
          Stop now
        </Button>
        <span className="text-[12px] text-ink-500">Takes effect on the next action. Anything already waiting for approval is checked again when approved.</span>
      </div>
      <ErrorText error={stop.error} fallback="Could not stop agents" />
    </form>
  )
}

export function StopsPage() {
  const canManage = hasPermission(usePermissions(), "agents.manage")
  const qc = useQueryClient()
  const [history, setHistory] = useState(false)
  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get<AgentSummary[]>("/agents")).data,
  })
  const { data, isLoading, error } = useQuery({
    queryKey: ["stops", history],
    queryFn: async () => (await api.get<Stop[]>("/kill-switches", { params: { include_released: history } })).data,
  })
  const release = useMutation({
    mutationFn: async (id: string) => api.post(`/kill-switches/${id}/release`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["stops"] }); qc.invalidateQueries({ queryKey: ["agents"] }) },
  })

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_420px] gap-4 items-start">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-[12.5px] text-ink-500">Kill-switches that are on right now. A stopped agent does nothing until someone lets it run again.</p>
          <label className="flex items-center gap-1 text-[12px] text-ink-500 shrink-0">
            <input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} /> Show past stops
          </label>
        </div>
        {isLoading && <Loading />}
        <ErrorText error={error ?? release.error} fallback="Could not load stops" />
        {data && (
          <Card>
            {data.length === 0 ? <Empty>No agent is stopped.</Empty> : (
              <ul className="divide-y divide-line">
                {data.map((s) => (
                  <li key={s.id} className="flex items-start gap-3 p-3 text-[13px]">
                    <Tag variant={s.released_at ? "neutral" : "red"} className="mt-0.5">{s.released_at ? "Ended" : "On"}</Tag>
                    <div className="flex-1">
                      <div className="font-semibold text-ink-900">{s.target}</div>
                      <div className="text-ink-700">{s.reason}</div>
                      <div className="text-[11.5px] text-ink-300">
                        <Actor actor={s.created_by} /> · {formatDateTime(s.created_at)}
                        {s.released_at && <> · ended by <Actor actor={s.released_by} /> {formatDateTime(s.released_at)}</>}
                      </div>
                    </div>
                    {canManage && !s.released_at && (
                      <Button size="sm" variant="secondary" onClick={() => release.mutate(s.id)}>Let them run</Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
      {canManage && agents.data && (
        <Card>
          <CardHeader><CardTitle>Stop agents</CardTitle></CardHeader>
          <CardContent><StopForm agents={agents.data} /></CardContent>
        </Card>
      )}
    </div>
  )
}
