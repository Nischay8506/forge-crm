import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Field, Input, Select, inputClass } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { useDebounced } from "@/lib/useDebounced"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { CostView } from "./CostView"
import { useCostModel, useCostModels } from "./hooks"
import { rupees } from "./labels"
import { type Layout, parsePasted, tableText } from "./tables"
import type { CostCheck, CostModelData, CostModelDetail, CostModelVersion } from "./types"

const UNITS = ["kg", "g", "tonne", "m", "mm", "m2", "m3", "litre", "nos"]

/** Each product family's cost model (F3.2, K3 to K8 A): which families have one, and
 *  whether it is a full model or only a rate card. */
export function CostModelsPage() {
  const { data, isLoading, error } = useCostModels()
  return (
    <div className="space-y-3">
      <p className="text-[13px] text-ink-500 max-w-2xl">
        How each family's cost is built up: lookup tables, materials with yield and scrap, machine minutes, overheads,
        packing and tooling. A family without one is costed from the item's ERPNext BOM, then its rate card; a line
        nothing covers goes to review in Decisions.
      </p>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the cost models" />
      {data && data.families.length === 0 && <Empty>No product families yet. Add them under Enquiries → Setup.</Empty>}
      {data && data.families.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[520px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-line">
                <th className="py-2 px-3 font-semibold">Family</th>
                <th className="py-2 pr-2 font-semibold">Cost model</th>
                <th className="py-2 pr-2 font-semibold">Saved</th>
                <th className="py-2 pr-3" />
              </tr>
            </thead>
            <tbody>
              {data.families.map((f) => (
                <tr key={f.id} className="border-b border-line last:border-0">
                  <td className="py-2 px-3 text-ink-900">{f.name}
                    {f.options_version === null && <span className="block text-[11.5px] text-ink-300">no options yet</span>}
                  </td>
                  <td className="py-2 pr-2">
                    {f.cost_model ? <KindTag v={f.cost_model} /> : <span className="text-ink-300">none</span>}
                  </td>
                  <td className="py-2 pr-2 text-ink-500 whitespace-nowrap">{f.cost_model ? `v${f.cost_model.version} · ${formatDateTime(f.cost_model.created_at)}` : "–"}</td>
                  <td className="py-2 pr-3 text-right"><Link to={`/quotes/cost-models/${f.id}`} className="text-teal-600 hover:underline">Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}

function KindTag({ v }: { v: CostModelVersion }) {
  return v.full ? <Tag variant="teal">Full model{v.rate_card ? " + rate card" : ""}</Tag> : <Tag variant="amber">Rate card only</Tag>
}

/** One family's cost model: the current version, earlier ones, and the editor. */
export function CostModelPage() {
  const { familyId } = useParams()
  const { data, isLoading, error } = useCostModel(familyId)
  const manage = hasPermission(usePermissions(), "costs.manage")
  const [draft, setDraft] = useState<CostModelData | null>(null)
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the cost model" />
  return (
    <div className="space-y-3">
      <div className="text-[12.5px]"><Link to="/quotes/cost-models" className="text-teal-600 hover:underline">← Cost models</Link></div>
      <h2 className="text-lg font-bold text-ink-900">{data.family.name}</h2>
      {draft
        ? <Editor d={data} start={draft} onDone={() => setDraft(null)} />
        : <Current d={data} manage={manage} onEdit={setDraft} />}
    </div>
  )
}

function Current({ d, manage, onEdit }: { d: CostModelDetail; manage: boolean; onEdit: (m: CostModelData) => void }) {
  const qc = useQueryClient()
  const [old, setOld] = useState<number | null>(null)
  const sample = useMutation({
    mutationFn: async () => (await api.post<CostModelVersion & { sample_rates: string[] }>(`/costs/models/${d.family.id}/sample`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["costs"] }),
  })
  const cur = d.current
  if (!cur) {
    return (
      <Card className="p-4 space-y-2 text-[13px]">
        <p className="text-ink-500">No cost model yet: lines of {d.family.name} are costed from the item's ERPNext BOM if it has one, else they go to review.</p>
        {d.options_version === null && <p className="text-amber-600">Set up the family's options first: a cost model works from them.</p>}
        {manage && (
          <div className="flex flex-wrap gap-2">
            {d.sample && <Button size="sm" variant="secondary" disabled={sample.isPending} onClick={() => sample.mutate()}>Start from the flanges sample</Button>}
            <Button size="sm" variant="secondary" onClick={() => onEdit({ values: [], materials: [], operations: [], tooling: [], tables: {} })}>Start empty</Button>
          </div>
        )}
        {sample.error && <ErrorText error={sample.error} fallback="Could not add the sample" />}
      </Card>
    )
  }
  const shown = old === null ? cur : d.versions.find((v) => v.version === old) ?? cur
  return (
    <div className="space-y-3">
      {sample.data && sample.data.sample_rates.length > 0 && (
        <p role="status" className="rounded-md border border-teal-500 bg-teal-100 px-3 py-2 text-[13px]">
          Added sample rates for {sample.data.sample_rates.join(", ")}. Replace them with your own on the rate screens.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <Tag variant="teal">Version {shown.version}</Tag>
        <KindTag v={shown} />
        <span className="text-ink-500">{shown.note ?? ""} · {formatDateTime(shown.created_at)}</span>
        <span className="flex-1" />
        {manage && old === null && <Button size="sm" variant="ghost" onClick={() => onEdit(JSON.parse(JSON.stringify(cur.model)))}>Edit the cost model</Button>}
      </div>
      {old !== null && <p className="text-[12.5px] text-amber-600">An earlier version, kept as it was. <button type="button" className="text-teal-600 hover:underline" onClick={() => setOld(null)}>Back to the current one</button></p>}
      {shown.model && <ModelSummary m={shown.model} />}
      {d.versions.length > 1 && (
        <Card className="p-3 text-[12.5px]">
          <p className="font-semibold text-ink-700 mb-1">Versions</p>
          <ul className="space-y-0.5">
            {d.versions.map((v) => (
              <li key={v.version}>
                <button type="button" className={cn("hover:underline", v.version === shown.version ? "text-ink-900 font-semibold" : "text-teal-600")}
                  onClick={() => setOld(v.current ? null : v.version)}>Version {v.version}</button>
                <span className="text-ink-500"> · {formatDateTime(v.created_at)}{v.note ? ` · ${v.note}` : ""}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function Rows({ title, rows }: { title: string; rows: [string, string][] }) {
  if (rows.length === 0) return null
  return (
    <div>
      <p className="font-semibold text-ink-700">{title}</p>
      <table className="w-full"><tbody>
        {rows.map(([a, b], i) => (
          <tr key={i} className="align-top border-b border-dashed border-line last:border-0">
            <td className="py-1 pr-3 text-ink-900 w-1/3">{a}</td>
            <td className="py-1 font-mono text-[12px] text-ink-700 break-all">{b}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}

function ModelSummary({ m }: { m: CostModelData }) {
  const [table, setTable] = useState<string | null>(null)
  const tables = Object.entries(m.tables ?? {})
  return (
    <Card className="p-4 space-y-3 text-[13px]">
      {tables.length > 0 && (
        <div>
          <p className="font-semibold text-ink-700">Tables</p>
          <div className="flex flex-wrap gap-2">
            {tables.map(([name, t]) => (
              <button key={name} type="button" onClick={() => setTable(table === name ? null : name)}
                className={cn("rounded-full border px-3 py-1 text-[12.5px]", table === name ? "border-teal-500 bg-teal-100" : "border-line text-ink-700")}>
                {name} <span className="text-ink-500">by {t.keys.join(" and ")} · {t.rows.length} rows</span>
              </button>
            ))}
          </div>
          {table && m.tables?.[table] && <TableGrid text={tableText(m.tables[table], m.tables[table].keys.length === 2 ? "grid" : "list")} />}
        </div>
      )}
      <Rows title="Values" rows={(m.values ?? []).map((v) => [`${v.label ?? v.name} (${v.name})`, v.formula])} />
      <Rows title="Materials per unit" rows={(m.materials ?? []).map((x) => [x.label, `${x.code} · ${x.qty} ${x.unit ?? "kg"}`])} />
      <Rows title="Operations (minutes per unit)" rows={(m.operations ?? []).map((x) => [x.label, `${x.machine} · ${x.minutes}`])} />
      <Rows title={m.tooling_spread ? "Tooling (spread over the quantity)" : "Tooling (one-time)"} rows={(m.tooling ?? []).map((x) => [x.label, x.amount])} />
      <Rows title="Overheads, packing, freight weights" rows={[
        ...(m.overhead_pct ? [["Factory overhead %", m.overhead_pct] as [string, string]] : []),
        ...(m.packing ? [[m.packing.basis === "pct" ? "Packing %" : "Packing per unit (₹)", m.packing.value] as [string, string]] : []),
        ...(m.packed_kg ? [["Packed weight (kg)", m.packed_kg] as [string, string]] : []),
        ...(m.packed_m3 ? [["Packed volume (m³)", m.packed_m3] as [string, string]] : []),
      ]} />
      {m.rate_card && <Rows title="Rate card (used when there is no full model or BOM)" rows={[
        [m.rate_card.basis === "kg" ? "Rate per kg (₹)" : "Rate per unit (₹)", m.rate_card.rate],
        ...(m.rate_card.weight ? [["Weight (kg)", m.rate_card.weight] as [string, string]] : []),
      ]} />}
    </Card>
  )
}

function TableGrid({ text }: { text: string }) {
  const lines = text.split("\n").map((l) => l.split("\t"))
  return (
    <div className="mt-2 overflow-x-auto max-h-80 overflow-y-auto border border-line rounded-md">
      <table className="text-[12px] font-mono">
        <tbody>
          {lines.map((cells, i) => (
            <tr key={i} className={cn(i === 0 && "bg-surface-2 font-semibold")}>
              {cells.map((c, j) => <td key={j} className={cn("px-2 py-0.5 border-b border-line text-right whitespace-nowrap", j === 0 && "bg-surface-2 font-semibold text-left")}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// The editor -----------------------------------------------------------------------------

type TableDraft = { name: string; keys: string; layout: Layout; text: string }

function toDrafts(m: CostModelData): TableDraft[] {
  return Object.entries(m.tables ?? {}).map(([name, t]) => {
    const layout: Layout = t.keys.length === 2 ? "grid" : "list"
    return { name, keys: t.keys.join(", "), layout, text: tableText(t, layout) }
  })
}

function keysOf(t: TableDraft): string[] {
  return t.keys.split(",").map((k) => k.trim()).filter(Boolean)
}

function build(m: CostModelData, tables: TableDraft[]): { model: CostModelData; tableProblems: string[] } {
  const out: CostModelData = { ...m, tables: {} }
  const tableProblems: string[] = []
  for (const t of tables) {
    const { table, problems } = parsePasted(t.text, keysOf(t), t.layout)
    out.tables![t.name.trim()] = table
    tableProblems.push(...problems.map((p) => `Table ${t.name || "(no name)"}: ${p}`))
  }
  return { model: out, tableProblems }
}

function Editor({ d, start, onDone }: { d: CostModelDetail; start: CostModelData; onDone: () => void }) {
  const qc = useQueryClient()
  const [m, setM] = useState<CostModelData>(start)
  const [tables, setTables] = useState<TableDraft[]>(() => toDrafts(start))
  const [note, setNote] = useState("")
  const [tryWith, setTryWith] = useState<Record<string, string> | null>(null)
  const [qty, setQty] = useState("10")
  const { model, tableProblems } = build(m, tables)
  const checked = useDebounced(JSON.stringify({ model, values: tryWith, qty: qty || null }), 600)
  const check = useQuery({
    queryKey: ["costs", "check", d.family.id, checked],
    queryFn: async () => (await api.post<CostCheck>(`/costs/models/${d.family.id}/check`, JSON.parse(checked))).data,
    staleTime: Infinity,
  })
  const save = useMutation({
    mutationFn: async () => api.put(`/costs/models/${d.family.id}`, { model, note: note.trim() || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["costs"] }); qc.invalidateQueries({ queryKey: ["quote"] }); onDone() },
  })
  const stale = check.isFetching || checked !== JSON.stringify({ model, values: tryWith, qty: qty || null })
  const problems = [...tableProblems, ...(save.error ? errorProblems(save.error) : (check.data?.problems ?? []))]
  const warnings = check.data?.warnings ?? []
  const preview = check.data?.preview
  const names = [...d.attributes.map((a) => a.key), "qty", ...(m.values ?? []).map((v) => v.name)]
  type ListKey = "values" | "materials" | "operations" | "tooling"
  const setList = <K extends ListKey>(k: K, rows: NonNullable<CostModelData[K]>) => setM({ ...m, [k]: rows })
  return (
    <div className="space-y-4 text-[13px]">
      <p className="text-ink-500 max-w-3xl">
        Formulas are written like Excel: <span className="font-mono text-ink-700">lookup(weight, size, class) * 1.05</span>,{" "}
        <span className="font-mono text-ink-700">if(facing == "RTJ", 12, 0)</span>. They can use {names.map((n, i) => <span key={n}>{i > 0 && ", "}<span className="font-mono text-ink-700">{n}</span></span>)}.
        Every change is tried on every valid configuration before it can be saved.
      </p>

      <Section title="Tables" hint="Copy the cells in Excel and paste them here. For two keys, paste a grid with the second key across the top.">
        {tables.map((t, i) => (
          <div key={i} className="rounded-md border border-line p-2.5 space-y-2">
            <div className="grid gap-2 grid-cols-2 sm:grid-cols-[160px_1fr_160px_auto] items-end">
              <Field label="Name"><Input className="font-mono" value={t.name} maxLength={30} placeholder="weight"
                onChange={(e) => setTables(tables.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} /></Field>
              <Field label="Keys" hint="Attributes, comma between"><Input className="font-mono" value={t.keys} placeholder="size, class"
                onChange={(e) => setTables(tables.map((x, j) => j === i ? { ...x, keys: e.target.value } : x))} /></Field>
              <Field label="Layout">
                <Select value={t.layout} onChange={(e) => setTables(tables.map((x, j) => {
                  if (j !== i) return x
                  const layout = e.target.value as Layout
                  const parsed = parsePasted(x.text, keysOf(x), x.layout).table
                  return { ...x, layout, text: parsed.rows.length ? tableText(parsed, layout) : x.text }
                }))}>
                  <option value="grid">Grid (two keys)</option>
                  <option value="list">One row per entry</option>
                </Select>
              </Field>
              <button type="button" className="text-[12px] text-red-500 pb-2" onClick={() => setTables(tables.filter((_, j) => j !== i))}>Remove</button>
            </div>
            <textarea className={cn(inputClass, "font-mono text-[12px] h-32 whitespace-pre")} value={t.text} spellCheck={false}
              aria-label={`Table ${t.name}`} placeholder={t.layout === "grid" ? "size\\class\t150\t300\n15\t0.9\t0.9" : "F316\t410"}
              onChange={(e) => setTables(tables.map((x, j) => j === i ? { ...x, text: e.target.value } : x))} />
            <p className="text-[11.5px] text-ink-300">{parsePasted(t.text, keysOf(t), t.layout).table.rows.length} rows read</p>
          </div>
        ))}
        {tables.length < 20 && <AddLink onClick={() => setTables([...tables, { name: "", keys: "", layout: "grid", text: "" }])}>+ Add a table</AddLink>}
      </Section>

      <Section title="Values" hint="Named results to use further down, worked out in this order">
        {(m.values ?? []).map((v, i) => (
          <RowEdit key={i} onRemove={() => setList("values", (m.values ?? []).filter((_, j) => j !== i))} cols="sm:grid-cols-[150px_1fr_2fr_auto]">
            <Input className="font-mono" aria-label="Name" placeholder="finished_kg" value={v.name} onChange={(e) => setList("values", (m.values ?? []).map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
            <Input aria-label="Label" placeholder="Finished weight (kg)" value={v.label ?? ""} onChange={(e) => setList("values", (m.values ?? []).map((x, j) => j === i ? { ...x, label: e.target.value } : x))} />
            <Input className="font-mono" aria-label="Formula" placeholder="lookup(weight, size, class)" value={v.formula} onChange={(e) => setList("values", (m.values ?? []).map((x, j) => j === i ? { ...x, formula: e.target.value } : x))} />
          </RowEdit>
        ))}
        <AddLink onClick={() => setList("values", [...(m.values ?? []), { name: "", label: "", formula: "" }])}>+ Add a value</AddLink>
      </Section>

      <Section title="Materials per unit" hint="The code is a material's code (F316) or a formula giving one (material). Yield and scrap come with the material's rate.">
        {(m.materials ?? []).map((x, i) => (
          <RowEdit key={i} onRemove={() => setList("materials", (m.materials ?? []).filter((_, j) => j !== i))} cols="sm:grid-cols-[1fr_1fr_1.5fr_90px_auto]">
            <Input aria-label="Material" placeholder="Forging" value={x.label} onChange={(e) => setList("materials", (m.materials ?? []).map((y, j) => j === i ? { ...y, label: e.target.value } : y))} />
            <Input className="font-mono" aria-label="Code" placeholder="material" value={x.code} onChange={(e) => setList("materials", (m.materials ?? []).map((y, j) => j === i ? { ...y, code: e.target.value } : y))} />
            <Input className="font-mono" aria-label="Quantity" placeholder="finished_kg" value={x.qty} onChange={(e) => setList("materials", (m.materials ?? []).map((y, j) => j === i ? { ...y, qty: e.target.value } : y))} />
            <Input aria-label="Unit" list="cost-units" placeholder="kg" value={x.unit ?? ""} onChange={(e) => setList("materials", (m.materials ?? []).map((y, j) => j === i ? { ...y, unit: e.target.value || undefined } : y))} />
          </RowEdit>
        ))}
        <AddLink onClick={() => setList("materials", [...(m.materials ?? []), { label: "", code: "", qty: "", unit: "kg" }])}>+ Add a material</AddLink>
      </Section>

      <Section title="Operations" hint="Minutes per unit on a machine, costed at its hour rate (ERPNext's workstation first)">
        {(m.operations ?? []).map((x, i) => (
          <RowEdit key={i} onRemove={() => setList("operations", (m.operations ?? []).filter((_, j) => j !== i))} cols="sm:grid-cols-[1fr_1fr_2fr_auto]">
            <Input aria-label="Operation" placeholder="Turning" value={x.label} onChange={(e) => setList("operations", (m.operations ?? []).map((y, j) => j === i ? { ...y, label: e.target.value } : y))} />
            <Input aria-label="Machine" placeholder="CNC Lathe" value={x.machine} onChange={(e) => setList("operations", (m.operations ?? []).map((y, j) => j === i ? { ...y, machine: e.target.value } : y))} />
            <Input className="font-mono" aria-label="Minutes" placeholder="6 + finished_kg * 0.8" value={x.minutes} onChange={(e) => setList("operations", (m.operations ?? []).map((y, j) => j === i ? { ...y, minutes: e.target.value } : y))} />
          </RowEdit>
        ))}
        <AddLink onClick={() => setList("operations", [...(m.operations ?? []), { label: "", machine: "", minutes: "" }])}>+ Add an operation</AddLink>
      </Section>

      <Section title="Tooling" hint="Amounts in ₹. One-time by default, shown apart on the quote.">
        {(m.tooling ?? []).map((x, i) => (
          <RowEdit key={i} onRemove={() => setList("tooling", (m.tooling ?? []).filter((_, j) => j !== i))} cols="sm:grid-cols-[1fr_2fr_auto]">
            <Input aria-label="Tooling" placeholder="Ring gauge" value={x.label} onChange={(e) => setList("tooling", (m.tooling ?? []).map((y, j) => j === i ? { ...y, label: e.target.value } : y))} />
            <Input className="font-mono" aria-label="Amount" placeholder='if(facing == "RTJ", 6500, 0)' value={x.amount} onChange={(e) => setList("tooling", (m.tooling ?? []).map((y, j) => j === i ? { ...y, amount: e.target.value } : y))} />
          </RowEdit>
        ))}
        <div className="flex flex-wrap items-center gap-4">
          <AddLink onClick={() => setList("tooling", [...(m.tooling ?? []), { label: "", amount: "" }])}>+ Add tooling</AddLink>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={!!m.tooling_spread} onChange={(e) => setM({ ...m, tooling_spread: e.target.checked })} /> Spread tooling over the quantity</label>
        </div>
      </Section>

      <Section title="Overheads, packing and freight weights">
        <div className="grid gap-2 grid-cols-2 sm:grid-cols-4">
          <Field label="Factory overhead %" hint="Of material and machining"><Input className="font-mono" value={m.overhead_pct ?? ""} placeholder="12" onChange={(e) => setM({ ...m, overhead_pct: e.target.value || undefined })} /></Field>
          <Field label="Packing">
            <div className="flex gap-1">
              <Select className="w-24" aria-label="Packing basis" value={m.packing?.basis ?? "pct"} onChange={(e) => setM({ ...m, packing: { basis: e.target.value as "pct" | "per_unit", value: m.packing?.value ?? "" } })}>
                <option value="pct">%</option><option value="per_unit">₹/unit</option>
              </Select>
              <Input className="font-mono" aria-label="Packing" value={m.packing?.value ?? ""} placeholder="2" onChange={(e) => setM({ ...m, packing: e.target.value ? { basis: m.packing?.basis ?? "pct", value: e.target.value } : undefined })} />
            </div>
          </Field>
          <Field label="Packed weight (kg)"><Input className="font-mono" value={m.packed_kg ?? ""} placeholder="finished_kg * 1.03" onChange={(e) => setM({ ...m, packed_kg: e.target.value || undefined })} /></Field>
          <Field label="Packed volume (m³)"><Input className="font-mono" value={m.packed_m3 ?? ""} placeholder="finished_kg * 0.0004" onChange={(e) => setM({ ...m, packed_m3: e.target.value || undefined })} /></Field>
        </div>
      </Section>

      <Section title="Rate card" hint="An all-in cost, used when the materials and operations above are empty and the item has no ERPNext BOM">
        <div className="grid gap-2 grid-cols-2 sm:grid-cols-[130px_1fr_1fr]">
          <Field label="Per">
            <Select value={m.rate_card?.basis ?? "kg"} onChange={(e) => setM({ ...m, rate_card: { basis: e.target.value as "kg" | "unit", rate: m.rate_card?.rate ?? "", weight: m.rate_card?.weight } })}>
              <option value="kg">kg</option><option value="unit">unit</option>
            </Select>
          </Field>
          <Field label="Rate (₹)"><Input className="font-mono" value={m.rate_card?.rate ?? ""} placeholder='if(material == "F316", 560, 165)'
            onChange={(e) => setM({ ...m, rate_card: e.target.value ? { basis: m.rate_card?.basis ?? "kg", rate: e.target.value, weight: m.rate_card?.weight } : undefined })} /></Field>
          {(m.rate_card?.basis ?? "kg") === "kg" && (
            <Field label="Weight (kg)"><Input className="font-mono" value={m.rate_card?.weight ?? ""} placeholder="finished_kg"
              onChange={(e) => setM({ ...m, rate_card: { basis: "kg", rate: m.rate_card?.rate ?? "", weight: e.target.value || undefined } })} /></Field>
          )}
        </div>
      </Section>
      <datalist id="cost-units">{UNITS.map((u) => <option key={u} value={u} />)}</datalist>

      <div role="status" className={cn("rounded-md border p-2.5 space-y-1", problems.length ? "border-red-200 bg-red-50" : "border-line bg-surface-0")}>
        {problems.length === 0
          ? <p className="text-ink-500">{stale ? "Checking every configuration…" : `Checked on ${check.data?.tried ?? 0} configurations: every formula works.`}</p>
          : (
            <>
              <p className="font-semibold text-red-600">{problems.length === 1 ? "1 thing to fix" : `${problems.length} things to fix`} before saving</p>
              <ul className="list-disc pl-5 space-y-0.5 text-ink-700">{problems.slice(0, 20).map((p, i) => <li key={i}>{p}</li>)}</ul>
            </>
          )}
        {warnings.length > 0 && (
          <ul className="list-disc pl-5 space-y-0.5 text-amber-600">{warnings.map((w) => <li key={w}>{w}: costs that use it are incomplete until it has one</li>)}</ul>
        )}
      </div>

      {preview && problems.length === 0 && (
        <Card className="p-3 space-y-2">
          <div className="flex flex-wrap items-end gap-2 text-[12.5px]">
            <p className="font-semibold text-ink-900 w-full">Try it with today's rates</p>
            {d.attributes.filter((a) => a.values.length > 0).map((a) => (
              <Field key={a.key} label={a.label} className="w-28">
                <Select value={(tryWith ?? preview.inputs.values)[a.key] ?? ""} onChange={(e) => setTryWith({ ...(tryWith ?? preview.inputs.values), [a.key]: e.target.value })}>
                  {a.values.map((v) => <option key={v} value={v}>{v}</option>)}
                </Select>
              </Field>
            ))}
            <Field label="Quantity" className="w-24"><Input inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
            {preview.per_unit && <p className="pb-2 font-semibold">{rupees(preview.per_unit)} per unit</p>}
          </div>
          <CostView c={preview} />
        </Card>
      )}

      <Field label="What changed (optional)"><Input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="e.g. New forging weights from the supplier" /></Field>
      {save.error && problems.length === 0 && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending || stale || problems.length > 0}>
          {save.isPending ? "Saving…" : "Save as a new version"}
        </Button>
        <Button size="sm" variant="secondary" onClick={onDone}>Cancel</Button>
      </div>
      <p className="text-[11.5px] text-ink-300">Saving works out open draft quotes of this family again, and marks sent ones "cost changed: reprice recommended".</p>
    </div>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <div>
        <p className="font-semibold text-ink-900">{title}</p>
        {hint && <p className="text-[12px] text-ink-500">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function RowEdit({ cols, onRemove, children }: { cols: string; onRemove: () => void; children: React.ReactNode }) {
  return (
    <div className={cn("grid gap-1.5 grid-cols-1 items-center", cols)}>
      {children}
      <button type="button" className="text-[12px] text-red-500 px-1 justify-self-start" aria-label="Remove" onClick={onRemove}>✕</button>
    </div>
  )
}

function AddLink({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return <button type="button" className="text-[12.5px] text-teal-600 hover:underline" onClick={onClick}>{children}</button>
}
