import { useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { useOverview } from "./hooks"
import type { Connection, LoginCheck } from "./types"

interface Form {
  name: string
  base_url: string
  api_key: string
  api_secret: string
  time_zone: string
  company: string
}

const EMPTY: Form = { name: "", base_url: "", api_key: "", api_secret: "", time_zone: "", company: "" }

function settingsOf(f: Form): Record<string, string> {
  const s: Record<string, string> = {}
  if (f.time_zone.trim()) s.time_zone = f.time_zone.trim()
  if (f.company.trim()) s.company = f.company.trim()
  return s
}

function CheckResult({ r, typedTz }: { r: LoginCheck; typedTz: boolean }) {
  const a = r.about ?? {}
  if (r.ok) {
    return (
      <div className="rounded-md bg-green-100 text-ink-700 text-[13px] p-3 space-y-1">
        <p className="text-green-500 font-semibold">{r.message ?? "Connected"}</p>
        {a.user && <p>Logged in as {a.user}</p>}
        {a.versions?.erpnext && <p>ERPNext {a.versions.erpnext}{a.versions.frappe ? ` (Frappe ${a.versions.frappe})` : ""}</p>}
        {a.time_zone && a.time_zone_source === "erpnext" && <p>Time zone {a.time_zone} (read from ERPNext)</p>}
        {a.time_zone && a.time_zone_source !== "erpnext" && (typedTz
          ? <p>Time zone {a.time_zone} (from this form)</p>
          : <p>ERPNext did not share its time zone, so Forge will use {a.time_zone}. If ERPNext runs on another
              time zone, set it under More settings.</p>)}
      </div>
    )
  }
  return (
    <div role="alert" className="rounded-md bg-red-100 text-ink-700 text-[13px] p-3 space-y-1">
      <p className="text-red-500 font-semibold">{r.title ?? "Forge could not connect"}</p>
      {r.message && <p>{r.message}</p>}
      {r.fix && <p><b>What to do:</b> {r.fix}</p>}
    </div>
  )
}

export function ConnectionWizardPage() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const overview = useOverview()
  const [form, setForm] = useState<Form>(EMPTY)
  const [tested, setTested] = useState<LoginCheck | null>(null)
  const [entities, setEntities] = useState<Set<string> | null>(null)
  const [startSync, setStartSync] = useState(true)
  const [advanced, setAdvanced] = useState(false)

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm({ ...form, [k]: e.target.value })
    if (k !== "name") setTested(null) // a changed login must be tested again
  }
  const login = () => ({
    kind: "erpnext", base_url: form.base_url.trim(), api_key: form.api_key.trim(),
    api_secret: form.api_secret.trim(), settings: settingsOf(form),
  })
  const test = useMutation({
    mutationFn: async () => (await api.post<LoginCheck>("/integrations/test", login())).data,
    onSuccess: (r) => setTested(r),
  })
  const all = overview.data?.entities ?? []
  const chosen = entities ?? new Set(all.map((e) => e.entity))
  const save = useMutation({
    mutationFn: async () => (await api.post<Connection>("/integrations/connections", {
      ...login(), name: form.name.trim(), entities: [...chosen], start_sync: startSync,
    })).data,
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["integrations"] })
      nav(`/integrations/connections/${c.id}`)
    },
  })

  if (overview.isLoading) return <Loading />
  if (overview.data && !overview.data.credential_keys_configured) {
    return <p className="text-[13px] text-ink-500">Forge cannot store ERP logins until an administrator sets the key (see above).</p>
  }
  const filled = form.base_url.trim() && form.api_key.trim() && form.api_secret.trim()
  const problems = errorProblems(save.error)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-4 items-start">
      <div className="space-y-4">
        <Card>
          <CardHeader><CardTitle>1. Log in to ERPNext</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Name for this connection" hint="For example ERPNext Pune">
                <Input value={form.name} onChange={set("name")} placeholder="ERPNext Pune" />
              </Field>
              <Field label="ERPNext address" hint="The address you open ERPNext at">
                <Input value={form.base_url} onChange={set("base_url")} placeholder="https://erp.yourcompany.com" />
              </Field>
              <Field label="API key">
                <Input value={form.api_key} onChange={set("api_key")} autoComplete="off" spellCheck={false} />
              </Field>
              <Field label="API secret" hint="Stored encrypted; nobody can read it back">
                <Input type="password" value={form.api_secret} onChange={set("api_secret")} autoComplete="new-password" />
              </Field>
            </div>
            <button type="button" className="text-[12px] text-teal-600 hover:underline" onClick={() => setAdvanced((v) => !v)}>
              {advanced ? "Hide more settings" : "More settings (time zone, company)"}
            </button>
            {advanced && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <Field label="Time zone" hint="Leave empty to read it from ERPNext">
                  <Input value={form.time_zone} onChange={set("time_zone")} placeholder="Asia/Kolkata" />
                </Field>
                <Field label="Company" hint="Whose credit limits to read, if ERPNext has several companies">
                  <Input value={form.company} onChange={set("company")} />
                </Field>
              </div>
            )}
            <div className="flex items-center gap-3">
              <Button variant="secondary" disabled={!filled || test.isPending} onClick={() => test.mutate()}>
                {test.isPending ? "Testing…" : "Test connection"}
              </Button>
              {test.error && <span className="text-red-500 text-[13px]">{errorMessage(test.error, "Could not test")}</span>}
            </div>
            {tested && <CheckResult r={tested} typedTz={!!form.time_zone.trim()} />}
          </CardContent>
        </Card>

        <Card className={tested?.ok ? "" : "opacity-60"}>
          <CardHeader><CardTitle>2. Choose what Forge reads</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {all.map((e) => (
                <label key={e.entity} className="flex items-center gap-2 text-[13px]">
                  <input type="checkbox" disabled={!tested?.ok} checked={chosen.has(e.entity)}
                    onChange={(ev) => {
                      const next = new Set(chosen)
                      if (ev.target.checked) next.add(e.entity)
                      else next.delete(e.entity)
                      setEntities(next)
                    }} />
                  {e.label}
                </label>
              ))}
            </div>
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" disabled={!tested?.ok} checked={startSync} onChange={(e) => setStartSync(e.target.checked)} />
              Start the first full read now (it runs in the background; the page shows progress)
            </label>
            {save.error && (
              <div className="text-red-500 text-[13px]">
                {problems.length ? problems.map((p) => <p key={p}>{p}</p>) : errorMessage(save.error, "Could not save the connection")}
              </div>
            )}
            <div className="flex gap-2">
              <Button disabled={!tested?.ok || !form.name.trim() || chosen.size === 0 || save.isPending} onClick={() => save.mutate()}>
                {save.isPending ? "Saving…" : "Save connection"}
              </Button>
              <Link to="/integrations/connections"><Button variant="secondary">Cancel</Button></Link>
            </div>
            {!tested?.ok && <p className="text-[12px] text-ink-300">Test the login first.</p>}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Getting the API key in ERPNext</CardTitle></CardHeader>
        <CardContent>
          <ol className="list-decimal pl-4 space-y-2 text-[12.5px] text-ink-700">
            <li>In ERPNext, open <b>User</b> and add a user for Forge (for example forge@yourcompany.com).</li>
            <li>Give it the roles <b>Sales User</b>, <b>Accounts User</b>, <b>Stock User</b>, <b>Sales Master Manager</b> and <b>Manufacturing User</b> (for BOMs and workstations).</li>
            <li>Open that user, go to <b>Settings › API Access</b> and click <b>Generate Keys</b>.</li>
            <li>Copy the API key and the API secret into this form. ERPNext shows the secret only once.</li>
            <li>Click <b>Test connection</b>. If something is missing, Forge says what to fix.</li>
          </ol>
          <p className="text-[12px] text-ink-300 mt-3">
            Forge only reads from ERPNext. The one thing it can write is “create this account as a customer”, and
            only when someone asks for it.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
