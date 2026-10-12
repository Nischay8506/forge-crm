import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { formatDateTime } from "@/lib/utils"
import { useFamilies } from "./hooks"
import type { Capability, Family, LimitKey, Process } from "./types"

const LIMIT_KEYS: LimitKey[] = ["max_thickness_mm", "max_length_mm", "max_diameter_mm", "max_weight_kg"]

type Lists = { materials: string; certifications: string; excluded_industries: string; target_industries: string }
type Row = { name: string; tolerance: string; families: string[] } & Record<LimitKey, string>

const LIST_FIELDS: { key: keyof Lists; label: string; hint: string }[] = [
  { key: "materials", label: "Materials and grades we work with", hint: "A grade not on this list needs an engineer. Separated by commas." },
  { key: "certifications", label: "Certificates we hold", hint: "An enquiry asking for one we don't hold (IBR, PED, ASME…) needs an engineer." },
  { key: "excluded_industries", label: "Industries we don't supply", hint: "An enquiry from one of these is no fit." },
  { key: "target_industries", label: "Industries we want more of", hint: "Raises the priority (Fit)." },
]

function toRow(p: Process): Row {
  const n = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v))
  return {
    name: p.name, tolerance: p.tolerance ?? "", families: p.families ?? [],
    max_thickness_mm: n(p.max_thickness_mm), max_length_mm: n(p.max_length_mm),
    max_diameter_mm: n(p.max_diameter_mm), max_weight_kg: n(p.max_weight_kg),
  }
}

function lists(c: Pick<Capability, keyof Lists>): Lists {
  return {
    materials: c.materials.join(", "), certifications: c.certifications.join(", "),
    excluded_industries: c.excluded_industries.join(", "), target_industries: c.target_industries.join(", "),
  }
}

const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean)

/** What we make (F1.6, N8 A): processes with their limits, materials, certificates,
 *  industries, and the engineering role that reviews what the rules can't place. */
export function CapabilitySetup({ canManage }: { canManage: boolean }) {
  const { data, isLoading } = useQuery({
    queryKey: ["enquiries", "capability"],
    queryFn: async () => (await api.get<Capability>("/enquiries/setup/capability")).data,
  })
  const families = useFamilies()
  if (isLoading || !data || !families.data) return <Loading />
  return <Form c={data} families={families.data.families} canManage={canManage} />
}

function Form({ c, families, canManage }: { c: Capability; families: Family[]; canManage: boolean }) {
  const qc = useQueryClient()
  const roles = useQuery({
    queryKey: ["roles"],
    queryFn: async () => (await api.get<{ id: string; name: string }[]>("/roles")).data,
    enabled: canManage,
    retry: false,
  })
  const [rows, setRows] = useState<Row[]>(c.processes.map(toRow))
  const [v, setV] = useState<Lists>(lists(c))
  const [role, setRole] = useState(c.engineering_role_id ?? "")
  const save = useMutation({
    mutationFn: async () => (await api.put<Capability>("/enquiries/setup/capability", {
      processes: rows.map((r) => ({
        name: r.name, tolerance: r.tolerance || null, families: r.families,
        ...Object.fromEntries(LIMIT_KEYS.map((k) => [k, r[k].trim() === "" ? null : r[k].trim()])),
      })),
      materials: split(v.materials), certifications: split(v.certifications),
      excluded_industries: split(v.excluded_industries), target_industries: split(v.target_industries),
      engineering_role_id: role || null,
    })).data,
    onSuccess: (data) => qc.setQueryData(["enquiries", "capability"], data),
  })
  const useExample = () => {
    const byTemplate = (keys: string[] = []) => families.filter((f) => f.template && keys.includes(f.template)).map((f) => f.id)
    setRows(c.example.processes.map((p) => toRow({ ...p, families: byTemplate(p.family_templates) })))
    setV(lists(c.example))
  }
  const setRow = (i: number, patch: Partial<Row>) => setRows(rows.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const toggle = (i: number, id: string) => {
    const has = rows[i].families.includes(id)
    setRow(i, { families: has ? rows[i].families.filter((x) => x !== id) : [...rows[i].families, id] })
  }

  return (
    <div className="space-y-4 max-w-[900px]">
      <Card className="p-4 space-y-2 text-[13px]">
        <p className="text-ink-700">
          Forge checks each enquiry against this and each product family's usual way of supplying it (set under Product families):
          standard, configurable, needs engineering, or no fit. The rules never decline on a limit: anything beyond it goes to an engineer.
          New enquiries are checked with this; on an older one, use <b>Check again</b>.
        </p>
        {canManage && (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary" onClick={useExample}>Fill in the sample steel and fabrication company</Button>
            <span className="text-[12px] text-ink-300">Fills the form only; change it to your own, then save.</span>
          </div>
        )}
        {c.updated_at && <p className="text-[11.5px] text-ink-300">Last saved by {c.updated_by} on {formatDateTime(c.updated_at)}</p>}
      </Card>

      <Card>
        <CardHeader className="pb-2"><CardTitle>Processes and their limits</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-3 text-[13px]">
          {rows.length === 0 && <p className="text-ink-500">No processes yet. Without them, only materials, certificates and families are checked.</p>}
          {rows.map((r, i) => (
            <div key={i} className="rounded-md border border-line p-3 space-y-2">
              <div className="grid gap-2 sm:grid-cols-[1fr_140px_auto] items-end">
                <Field label="Process"><Input value={r.name} onChange={(e) => setRow(i, { name: e.target.value })} maxLength={100} disabled={!canManage} placeholder="e.g. Plate cutting" /></Field>
                <Field label="Tolerance"><Input value={r.tolerance} onChange={(e) => setRow(i, { tolerance: e.target.value })} maxLength={60} disabled={!canManage} placeholder="e.g. ±1 mm" /></Field>
                {canManage && <button type="button" className="text-[12px] text-red-500 pb-2" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>}
              </div>
              <div className="grid gap-2 grid-cols-2 sm:grid-cols-4">
                {LIMIT_KEYS.map((k) => (
                  <Field key={k} label={c.limits[k]}>
                    <Input value={r[k]} onChange={(e) => setRow(i, { [k]: e.target.value } as Partial<Row>)} inputMode="decimal" disabled={!canManage} placeholder="No limit" />
                  </Field>
                ))}
              </div>
              <Field label="Applies to" hint="None ticked: every product family" group>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {families.map((f) => (
                    <label key={f.id} className="flex items-center gap-1.5">
                      <input type="checkbox" checked={r.families.includes(f.id)} onChange={() => toggle(i, f.id)} disabled={!canManage} /> {f.name}
                    </label>
                  ))}
                  {families.length === 0 && <span className="text-ink-300">No product families yet</span>}
                </div>
              </Field>
            </div>
          ))}
          {canManage && rows.length < 40 && (
            <button type="button" className="text-[12.5px] text-teal-600 hover:underline"
              onClick={() => setRows([...rows, toRow({ name: "" })])}>+ Add a process</button>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader className="pb-2"><CardTitle>Materials, certificates and industries</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-3 text-[13px]">
          {LIST_FIELDS.map((f) => (
            <Field key={f.key} label={f.label} hint={f.hint}>
              <textarea value={v[f.key]} onChange={(e) => setV({ ...v, [f.key]: e.target.value })} rows={2} disabled={!canManage}
                className="w-full rounded-md border border-input bg-surface-1 px-3 py-2 text-[13px] disabled:opacity-60" />
            </Field>
          ))}
          <Field label="Engineering role" hint="Reviews enquiries that need engineering. Empty: the escalation people under Decisions → Deadlines are told.">
            <Select value={role} onChange={(e) => setRole(e.target.value)} disabled={!canManage}>
              <option value="">Not set</option>
              {roles.data?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              {!roles.data && c.engineering_role_id && <option value={c.engineering_role_id}>{c.engineering_role ?? "Current role"}</option>}
            </Select>
          </Field>
        </div>
      </Card>

      {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
      {save.isSuccess && <p role="status" className="text-green-500 text-[12.5px]">Saved. New enquiries are checked with this.</p>}
      {canManage && <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save what we make</Button>}
    </div>
  )
}
