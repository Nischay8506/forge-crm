import { useState } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { HEALTH, RUN_TONES, SETTING_LABELS, ago, words } from "./labels"
import type { ConnectionDetail, DeadLetter, Flow } from "./types"

type Tab = "flows" | "runs" | "failed" | "settings"
const TABS: { key: Tab; label: string }[] = [
  { key: "flows", label: "Record types" },
  { key: "runs", label: "Recent runs" },
  { key: "failed", label: "Records that failed" },
  { key: "settings", label: "Settings and login" },
]

export function ConnectionDetailPage() {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = (params.get("tab") as Tab) || "flows"
  const perms = usePermissions()
  const canManage = hasPermission(perms, "integrations.manage")
  const qc = useQueryClient()
  const key = ["integrations", "connection", id]
  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<ConnectionDetail>(`/integrations/connections/${id}`)).data,
    // Running syncs show their progress without a reload.
    refetchInterval: (q) => (q.state.data?.flows.some((f) => f.running_since || f.run_requested_at) ? 5_000 : 30_000),
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ["integrations"] })
  const check = useMutation({
    mutationFn: async () => (await api.post<{ ok: boolean; message: string | null }>(`/integrations/connections/${id}/check`)).data,
    onSuccess: refresh,
  })
  const sync = useMutation({
    mutationFn: async (reconcile: boolean) =>
      (await api.post<{ requested: string[] }>(`/integrations/connections/${id}/sync`, { reconcile })).data,
    onSuccess: refresh,
  })
  const pause = useMutation({
    mutationFn: async (status: "active" | "paused") => api.patch(`/integrations/connections/${id}`, { status }),
    onSuccess: refresh,
  })

  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the connection" />
  const h = HEALTH[data.health]
  const active = data.status === "active"

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <Link to="/integrations/connections" className="text-[12px] text-teal-600 hover:underline">← All connections</Link>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-bold text-ink-900">{data.name}</h2>
            <Tag variant={h.tone}>{h.label}</Tag>
          </div>
          <p className="text-[12px] text-ink-500">
            ERPNext{data.about?.versions?.erpnext ? ` ${data.about.versions.erpnext}` : ""} · {data.base_url}
            {data.about?.user ? ` · logged in as ${data.about.user}` : ""} · key {data.key_hint}
          </p>
          <p className="text-[12px] text-ink-500">
            Login checked {ago(data.last_check.at)}
            {data.last_check.ok === false && data.last_ok_at ? `; last worked ${ago(data.last_ok_at)}` : ""}
          </p>
        </div>
        {canManage && (
          <div className="flex flex-wrap gap-2 justify-end shrink-0">
            <Button variant="secondary" size="sm" disabled={check.isPending} onClick={() => check.mutate()}>
              {check.isPending ? "Checking…" : "Check login now"}
            </Button>
            {active && (
              <>
                <Button size="sm" disabled={sync.isPending} onClick={() => sync.mutate(false)}>Read changes now</Button>
                <Button variant="secondary" size="sm" disabled={sync.isPending} onClick={() => sync.mutate(true)}
                  title="Compares every record with the ERP and repairs differences (also runs every night)">
                  Compare with ERP
                </Button>
              </>
            )}
            <Button variant={active ? "danger" : "gold"} size="sm" disabled={pause.isPending}
              onClick={() => pause.mutate(active ? "paused" : "active")}>
              {active ? "Pause" : "Resume"}
            </Button>
          </div>
        )}
      </div>
      {sync.data && (
        <p className="text-[12.5px] text-teal-600">
          Asked for {sync.data.requested.length} record type(s). The worker starts within a minute; this page updates on its own.
        </p>
      )}
      {check.data && (
        <p className={cn("text-[12.5px]", check.data.ok ? "text-green-500" : "text-red-500")}>{check.data.message}</p>
      )}
      <ErrorText error={check.error ?? sync.error ?? pause.error} fallback="Could not do that" />

      {data.problems.length > 0 && (
        <Card className="p-4 border-l-4 border-l-red-500 space-y-2" role="alert">
          {data.problems.map((p) => (
            <div key={p.code + (p.entity ?? "")} className="text-[13px] space-y-0.5">
              <p className="font-semibold text-red-500">{p.title}</p>
              <p className="text-ink-700">{p.detail}</p>
              {p.fix && <p className="text-ink-700"><b>What to do:</b> {p.fix}</p>}
            </div>
          ))}
          {data.alert_id && (
            <Link to="/decisions" className="text-[12px] text-teal-600 hover:underline">This alert is also in Decisions</Link>
          )}
        </Card>
      )}

      <nav className="flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setParams(t.key === "flows" ? {} : { tab: t.key })}
            className={cn("px-3 py-2 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900",
              tab === t.key && "text-ink-900 border-teal-500")}>
            {t.label}{t.key === "failed" && data.dead_letters > 0 ? ` (${data.dead_letters})` : ""}
          </button>
        ))}
      </nav>
      {tab === "flows" && <FlowsTable flows={data.flows} canManage={canManage} />}
      {tab === "runs" && <RunsTable data={data} />}
      {tab === "failed" && <DeadLetters connId={data.id} />}
      {tab === "settings" && <SettingsPanel data={data} canManage={canManage} />}
    </div>
  )
}

function FlowsTable({ flows, canManage }: { flows: Flow[]; canManage: boolean }) {
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: async (f: Flow) => api.patch(`/integrations/flows/${f.id}`, { enabled: !f.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations"] }),
  })
  return (
    <Card>
      <CardContent className="pt-4">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-left text-ink-500 border-b border-line">
              <th className="py-2 pr-3 font-semibold">Record type</th>
              <th className="py-2 pr-3 font-semibold">State</th>
              <th className="py-2 pr-3 font-semibold">Last read</th>
              <th className="py-2 pr-3 font-semibold text-right">Records</th>
              <th className="py-2 pr-3 font-semibold">Last compared</th>
              <th className="py-2 font-semibold" />
            </tr>
          </thead>
          <tbody>
            {flows.map((f) => {
              const rec = f.last_reconcile
              return (
                <tr key={f.id} className="border-b border-line last:border-0 align-top">
                  <td className="py-2 pr-3 font-medium">{f.label}</td>
                  <td className="py-2 pr-3">
                    {!f.enabled ? <Tag>Off</Tag>
                      : f.running_since ? <Tag variant="teal">Reading{f.resuming ? " (resuming)" : ""}</Tag>
                      : f.consecutive_failures > 0 ? (
                        <div className="space-y-0.5">
                          <Tag variant="red">Failing ({f.consecutive_failures})</Tag>
                          <p className="text-[11.5px] text-red-500">{f.last_error_message}</p>
                        </div>)
                      : f.run_requested_at ? <Tag variant="neutral">Starting soon</Tag>
                      : f.last_success_at ? <Tag variant="green">Up to date</Tag>
                      : <Tag>Not read yet</Tag>}
                  </td>
                  <td className="py-2 pr-3 text-ink-500" title={formatDateTime(f.last_success_at)}>{ago(f.last_success_at)}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{f.records_read.toLocaleString("en-IN")}</td>
                  <td className="py-2 pr-3 text-ink-500 text-[12px]">
                    {rec?.at ? (
                      <span title={formatDateTime(rec.at)}>
                        {ago(rec.at)}{rec.error ? <span className="text-red-500"> · {rec.error}</span>
                          : ` · ${rec.checked ?? 0} checked${rec.repaired ? `, ${rec.repaired} repaired` : ""}${rec.gone ? `, ${rec.gone} gone` : ""}`}
                      </span>
                    ) : "–"}
                  </td>
                  <td className="py-2 text-right">
                    {canManage && (
                      <button className="text-[12px] text-teal-600 hover:underline disabled:opacity-50" disabled={toggle.isPending}
                        onClick={() => toggle.mutate(f)}>
                        {f.enabled ? "Turn off" : "Turn on"}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <ErrorText error={toggle.error} fallback="Could not change it" />
      </CardContent>
    </Card>
  )
}

function RunsTable({ data }: { data: ConnectionDetail }) {
  const labels = Object.fromEntries(data.flows.map((f) => [f.entity, f.label]))
  if (data.runs.length === 0) return <Card><Empty>No runs yet.</Empty></Card>
  return (
    <Card>
      <CardContent className="pt-4">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="text-left text-ink-500 border-b border-line">
              <th className="py-2 pr-3 font-semibold">Started</th>
              <th className="py-2 pr-3 font-semibold">Record type</th>
              <th className="py-2 pr-3 font-semibold">Kind</th>
              <th className="py-2 pr-3 font-semibold">Result</th>
              <th className="py-2 pr-3 font-semibold text-right">Read</th>
              <th className="py-2 pr-3 font-semibold text-right">Failed</th>
              <th className="py-2 font-semibold">Note</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map((r) => (
              <tr key={r.id} className="border-b border-line last:border-0 align-top">
                <td className="py-2 pr-3 text-ink-500 whitespace-nowrap">{formatDateTime(r.started_at)}</td>
                <td className="py-2 pr-3">{r.entity ? labels[r.entity] ?? r.entity : "–"}</td>
                <td className="py-2 pr-3 text-ink-500">{r.kind === "reconcile" ? "Compare" : "Read"} · {words(r.trigger)}</td>
                <td className="py-2 pr-3"><Tag variant={RUN_TONES[r.status] ?? "neutral"}>{words(r.status)}</Tag></td>
                <td className="py-2 pr-3 text-right tabular-nums">{r.read}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{r.problems || ""}</td>
                <td className="py-2 text-[12px] text-ink-500">
                  {r.error_message ?? (r.retries ? `${r.retries} retr${r.retries === 1 ? "y" : "ies"}` : "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}

function DeadLetters({ connId }: { connId: string }) {
  const perms = usePermissions()
  const canMap = hasPermission(perms, "integrations.map")
  const qc = useQueryClient()
  const [status, setStatus] = useState<"open" | "skipped" | "resolved" | "all">("open")
  const [openRaw, setOpenRaw] = useState<string | null>(null)
  const key = ["integrations", "dead", connId, status]
  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<DeadLetter[]>(`/integrations/connections/${connId}/dead-letters`, { params: { status } })).data,
  })
  const act = useMutation({
    mutationFn: async ({ d, what }: { d: DeadLetter; what: "retry" | "skip" }) => {
      if (what === "skip") {
        const note = window.prompt(`Why skip ${d.external_id ?? "this record"}? (kept in the audit log)`, "")
        if (note === null) return null
        return (await api.post(`/integrations/dead-letters/${d.id}/skip`, { note: note || null })).data
      }
      return (await api.post<{ ok: boolean; reason?: string }>(`/integrations/dead-letters/${d.id}/retry`)).data
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations"] }),
  })
  const retryResult = act.data as { ok?: boolean; reason?: string } | null | undefined

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <p className="text-[12.5px] text-ink-500 max-w-[640px]">
          Records the ERP sent that Forge could not read. The rest of the sync carried on. Fix the record in the ERP
          and retry it, or skip it.
        </p>
        <Select className="w-[160px]" value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
          <option value="open">Waiting</option>
          <option value="skipped">Skipped</option>
          <option value="resolved">Fixed</option>
          <option value="all">All</option>
        </Select>
      </CardHeader>
      <CardContent className="space-y-2">
        {isLoading && <Loading />}
        <ErrorText error={error ?? act.error} fallback="Could not load the records" />
        {retryResult && retryResult.ok === false && (
          <p className="text-[12.5px] text-red-500">Still failing: {retryResult.reason}</p>
        )}
        {data && data.length === 0 && <Empty>Nothing here.</Empty>}
        {data?.map((d) => (
          <div key={d.id} className="rounded-md border border-line p-3 text-[13px] space-y-1">
            <div className="flex items-start justify-between gap-3">
              <div>
                <span className="font-semibold">{d.label}: {d.external_id ?? "(no id)"}</span>
                <p className="text-red-500">{d.reason}</p>
                <p className="text-[11.5px] text-ink-300">
                  Seen {d.attempts} time{d.attempts === 1 ? "" : "s"}, first {formatDateTime(d.first_seen_at)}, last {ago(d.last_seen_at)}
                </p>
                {d.status !== "open" && (
                  <p className="text-[12px] text-ink-500 flex items-center gap-1.5">
                    <Tag>{d.status === "resolved" ? "Fixed" : "Skipped"}</Tag> <Actor actor={d.resolved_by} />
                    {d.note ? `: ${d.note}` : ""}
                  </p>
                )}
              </div>
              {canMap && d.status === "open" && (
                <div className="flex gap-2 shrink-0">
                  {d.external_id && (
                    <Button size="sm" variant="secondary" disabled={act.isPending} onClick={() => act.mutate({ d, what: "retry" })}>Retry</Button>
                  )}
                  <Button size="sm" variant="danger" disabled={act.isPending} onClick={() => act.mutate({ d, what: "skip" })}>Skip</Button>
                </div>
              )}
            </div>
            <button className="text-[12px] text-teal-600 hover:underline" onClick={() => setOpenRaw(openRaw === d.id ? null : d.id)}>
              {openRaw === d.id ? "Hide what the ERP sent" : "Show what the ERP sent"}
            </button>
            {openRaw === d.id && (
              <pre className="text-[11.5px] bg-surface-2 rounded-md p-2 overflow-auto max-h-64">{JSON.stringify(d.raw, null, 2)}</pre>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function SettingsPanel({ data, canManage }: { data: ConnectionDetail; canManage: boolean }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    name: data.name, base_url: data.base_url,
    time_zone: data.settings.time_zone ?? "", company: data.settings.company ?? "",
    bom_includes_overhead: data.settings.bom_includes_overhead === "yes",
    bom_includes_packing: data.settings.bom_includes_packing === "yes",
  })
  const [creds, setCreds] = useState({ api_key: "", api_secret: "" })
  const [hook, setHook] = useState<{ token: string; url: string; header: string } | null>(null)
  const done = () => qc.invalidateQueries({ queryKey: ["integrations"] })
  const save = useMutation({
    mutationFn: async () => api.patch(`/integrations/connections/${data.id}`, {
      name: form.name, base_url: form.base_url,
      settings: {
        ...data.settings, time_zone: form.time_zone || null, company: form.company || null,
        bom_includes_overhead: form.bom_includes_overhead ? "yes" : null,
        bom_includes_packing: form.bom_includes_packing ? "yes" : null,
      },
    }),
    onSuccess: done,
  })
  const login = useMutation({
    mutationFn: async () => (await api.put(`/integrations/connections/${data.id}/credentials`, creds)).data,
    onSuccess: () => { setCreds({ api_key: "", api_secret: "" }); done() },
  })
  const webhook = useMutation({
    mutationFn: async () => (await api.post<{ token: string; url: string; header: string }>(`/integrations/connections/${data.id}/webhook-token`)).data,
    onSuccess: (r) => { setHook(r); done() },
  })
  const saveProblems = errorProblems(save.error)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
      <Card>
        <CardHeader><CardTitle>Connection</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <Field label="Name"><Input disabled={!canManage} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="ERPNext address"><Input disabled={!canManage} value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></Field>
          <Field label={SETTING_LABELS.time_zone} hint={data.about?.time_zone_source === "erpnext" ? "Read from ERPNext" : "ERPNext's timestamps are read in this time zone"}>
            <Input disabled={!canManage} value={form.time_zone} onChange={(e) => setForm({ ...form, time_zone: e.target.value })} placeholder="Asia/Kolkata" />
          </Field>
          <Field label={SETTING_LABELS.company} hint="Only needed when ERPNext has several companies">
            <Input disabled={!canManage} value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} />
          </Field>
          <div className="space-y-1.5 pt-1">
            <p className="text-[12.5px] text-ink-500">
              When a quote line is costed from a BOM here, Forge adds the family's factory overhead and packing
              on top. Tick a box if this ERP's BOM costs already carry that, so it is not counted twice.
            </p>
            <label className="flex items-center gap-1.5 text-[12.5px]">
              <input type="checkbox" disabled={!canManage} checked={form.bom_includes_overhead}
                onChange={(e) => setForm({ ...form, bom_includes_overhead: e.target.checked })} />
              {SETTING_LABELS.bom_includes_overhead}
            </label>
            <label className="flex items-center gap-1.5 text-[12.5px]">
              <input type="checkbox" disabled={!canManage} checked={form.bom_includes_packing}
                onChange={(e) => setForm({ ...form, bom_includes_packing: e.target.checked })} />
              {SETTING_LABELS.bom_includes_packing}
            </label>
          </div>
          {save.error && (
            <div className="text-red-500 text-[12.5px]">
              {saveProblems.length ? saveProblems.map((p) => <p key={p}>{p}</p>) : errorMessage(save.error, "Could not save")}
            </div>
          )}
          {save.isSuccess && <p className="text-[12.5px] text-green-500">Saved.</p>}
          {canManage && <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>Save</Button>}
          <p className="text-[11.5px] text-ink-300">Added {formatDateTime(data.created_at)} by <Actor actor={data.created_by} /></p>
        </CardContent>
      </Card>

      <div className="space-y-4">
        <Card>
          <CardHeader><CardTitle>API key</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-[12.5px] text-ink-500">
              Stored encrypted. Forge shows only the end of the key ({data.key_hint}). When you generate new keys in
              ERPNext, enter both here; Forge checks them straight away.
            </p>
            {canManage && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <Input placeholder="New API key" autoComplete="off" value={creds.api_key} onChange={(e) => setCreds({ ...creds, api_key: e.target.value })} />
                  <Input placeholder="New API secret" type="password" autoComplete="new-password" value={creds.api_secret} onChange={(e) => setCreds({ ...creds, api_secret: e.target.value })} />
                </div>
                <ErrorText error={login.error} fallback="Could not change the key" />
                {login.isSuccess && <p className="text-[12.5px] text-green-500">Key changed and checked.</p>}
                <Button size="sm" disabled={!creds.api_key || !creds.api_secret || login.isPending} onClick={() => login.mutate()}>
                  Change key
                </Button>
              </>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>ERPNext webhook (optional)</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-[12.5px] text-ink-500">
              Forge already reads changes every few minutes. A webhook makes it read sooner after a change. In ERPNext
              add a Webhook for the record type with the address and header below.
              {data.webhook ? " A webhook secret is set; making a new one stops the old one." : ""}
            </p>
            {hook && (
              <div className="rounded-md bg-gold-100 p-3 text-[12.5px] space-y-1 break-all">
                <p><b>Address:</b> {hook.url}</p>
                <p><b>Header:</b> {hook.header}: {hook.token}</p>
                <p className="text-ink-500">Copy it now; Forge will not show this secret again.</p>
              </div>
            )}
            <ErrorText error={webhook.error} fallback="Could not make a webhook secret" />
            {canManage && (
              <Button size="sm" variant="secondary" disabled={webhook.isPending} onClick={() => webhook.mutate()}>
                {data.webhook ? "Make a new webhook secret" : "Set up a webhook"}
              </Button>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
