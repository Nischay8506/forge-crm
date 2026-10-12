import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { useFamilies } from "./hooks"
import { MAKE_LABELS, MAKE_TONE, qtyText } from "./labels"
import type { ChecklistItem, Family, MakeAbility } from "./types"

const UNITS = ["MT", "kg", "nos", "m", "sets"]

const MAKE_HINTS: Record<MakeAbility, string> = {
  standard: "We make or stock it as usual",
  cto: "A usual product with options the customer picks",
  eto: "Each one needs an engineer to look at it",
  no_fit: "We don't make it: such enquiries get a decline a person approves",
}

function sizeText(f: Family): string | null {
  const s = f.sweet_spot
  if (!s) return null
  if (s.min !== undefined && s.max !== undefined) return `${s.min} to ${s.max} ${s.unit}`
  if (s.min !== undefined) return `${s.min} ${s.unit} and up`
  if (s.max !== undefined) return `up to ${s.max} ${s.unit}`
  return null
}

/** Product families (N8 A): the words that point to a family, how we supply it (F1.6),
 *  its minimum and best order size (F1.7), and the checklist of details a quote needs.
 *  The acknowledgement asks the customer for what is missing. */
export function FamiliesSetup({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient()
  const { data, isLoading } = useFamilies()
  const [editing, setEditing] = useState<string | null>(null)
  const add = useMutation({
    mutationFn: async (template: string) => (await api.post<Family>("/enquiries/setup/families", { template })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["enquiries", "families"] }),
  })
  if (isLoading || !data) return <Loading />
  const used = new Set(data.families.map((f) => f.template))
  return (
    <div className="space-y-4 max-w-[860px]">
      {canManage && (
        <Card className="p-4 space-y-2 text-[13px]">
          <p className="font-semibold text-ink-900">Start from a template</p>
          <div className="flex flex-wrap gap-2">
            {data.templates.map((t) => (
              <Button key={t.key} size="sm" variant="secondary" disabled={used.has(t.key) || add.isPending} onClick={() => add.mutate(t.key)}>
                + {t.name}{used.has(t.key) ? " (added)" : ""}
              </Button>
            ))}
          </div>
          {add.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(add.error, "Could not add it")}</p>}
        </Card>
      )}
      {data.families.length === 0 && <p className="text-[13px] text-ink-500">No product families yet. Every enquiry uses the general checklist below.</p>}
      {data.families.map((f) => editing === f.id
        ? <FamilyForm key={f.id} f={f} onDone={() => setEditing(null)} />
        : (
          <Card key={f.id} className="p-4 space-y-2 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-ink-900">{f.name}</span>
              <Tag variant={MAKE_TONE[f.make_ability]} title={MAKE_HINTS[f.make_ability]}>{MAKE_LABELS[f.make_ability]}</Tag>
              {!f.active && <Tag>Not used</Tag>}
              <span className="flex-1" />
              {canManage && <Button size="sm" variant="ghost" onClick={() => setEditing(f.id)}>Edit</Button>}
            </div>
            <p className="text-ink-500"><span className="text-ink-700">Words:</span> {f.keywords.join(", ") || "–"}</p>
            {(f.moq || f.sweet_spot) && (
              <p className="text-ink-500">
                {f.moq && <><span className="text-ink-700">Minimum order:</span> {qtyText(String(f.moq.qty), f.moq.unit)}</>}
                {f.moq && f.sweet_spot && " · "}
                {sizeText(f) && <><span className="text-ink-700">Orders we like:</span> {sizeText(f)}</>}
              </p>
            )}
            {f.make_ability === "no_fit" && <p className="text-ink-500"><span className="text-ink-700">Refer customers to:</span> {f.refer_to ?? "nobody named"}</p>}
            <ChecklistView items={f.checklist} />
          </Card>
        ))}
      <Card className="p-4 space-y-2 text-[13px]">
        <p className="font-semibold text-ink-900">General checklist <span className="font-normal text-ink-500">(when no family fits)</span></p>
        <ChecklistView items={data.general} />
      </Card>
    </div>
  )
}

function ChecklistView({ items }: { items: ChecklistItem[] }) {
  return (
    <ul className="space-y-1">
      {items.map((i) => (
        <li key={i.key} className="flex gap-2">
          <span className="w-40 shrink-0 text-ink-700">{i.label}{i.required ? "" : " (if given)"}</span>
          <span className="text-ink-500">{i.questions.en}</span>
        </li>
      ))}
    </ul>
  )
}

function FamilyForm({ f, onDone }: { f: Family; onDone: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(f.name)
  const [keywords, setKeywords] = useState(f.keywords.join(", "))
  const [items, setItems] = useState<ChecklistItem[]>(f.checklist)
  const [active, setActive] = useState(f.active)
  const [make, setMake] = useState<MakeAbility>(f.make_ability)
  const n = (v: number | undefined | null) => (v === undefined || v === null ? "" : String(v))
  const [moq, setMoq] = useState({ qty: n(f.moq?.qty), unit: f.moq?.unit ?? "MT" })
  const [spot, setSpot] = useState({ min: n(f.sweet_spot?.min), max: n(f.sweet_spot?.max), unit: f.sweet_spot?.unit ?? "MT" })
  const [referTo, setReferTo] = useState(f.refer_to ?? "")
  const num = (v: string) => (v.trim() === "" ? undefined : v.trim())
  const save = useMutation({
    mutationFn: async () => api.put(`/enquiries/setup/families/${f.id}`, {
      name, active, checklist: items, make_ability: make,
      keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
      // An empty amount clears it.
      moq: num(moq.qty) ? { qty: num(moq.qty), unit: moq.unit } : {},
      sweet_spot: num(spot.min) || num(spot.max) ? { min: num(spot.min), max: num(spot.max), unit: spot.unit } : {},
      refer_to: referTo.trim(),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["enquiries", "families"] }); onDone() },
  })
  const setItem = (i: number, patch: Partial<ChecklistItem>) => setItems(items.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const setQ = (i: number, lang: "en" | "hi" | "te", text: string) => setItem(i, { questions: { ...items[i].questions, [lang]: text } })
  return (
    <Card className="border-teal-500">
      <CardHeader className="pb-2"><CardTitle>Edit {f.name}</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-3 text-[13px]">
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} /></Field>
          <Field label="Words that point to it" hint="Separated by commas"><Input value={keywords} onChange={(e) => setKeywords(e.target.value)} /></Field>
        </div>
        <label className="flex items-center gap-2"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> In use</label>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="How we supply it" hint={MAKE_HINTS[make]}>
            <Select value={make} onChange={(e) => setMake(e.target.value as MakeAbility)}>
              {(Object.keys(MAKE_LABELS) as MakeAbility[]).map((k) => <option key={k} value={k}>{MAKE_LABELS[k]}</option>)}
            </Select>
          </Field>
          {make === "no_fit" && (
            <Field label="Refer customers to" hint="Named in the decline, e.g. a partner company (optional)">
              <Input value={referTo} onChange={(e) => setReferTo(e.target.value)} maxLength={300} />
            </Field>
          )}
        </div>
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_1fr]">
          <Field label="Minimum order"><Input value={moq.qty} onChange={(e) => setMoq({ ...moq, qty: e.target.value })} inputMode="decimal" placeholder="None" /></Field>
          <Field label="Unit"><Select value={moq.unit} onChange={(e) => setMoq({ ...moq, unit: e.target.value })}>{UNITS.map((u) => <option key={u}>{u}</option>)}</Select></Field>
          <Field label="Orders we like, from"><Input value={spot.min} onChange={(e) => setSpot({ ...spot, min: e.target.value })} inputMode="decimal" placeholder="Any" /></Field>
          <Field label="to"><Input value={spot.max} onChange={(e) => setSpot({ ...spot, max: e.target.value })} inputMode="decimal" placeholder="Any" /></Field>
          <Field label="Unit"><Select value={spot.unit} onChange={(e) => setSpot({ ...spot, unit: e.target.value })}>{UNITS.map((u) => <option key={u}>{u}</option>)}</Select></Field>
        </div>
        <p className="text-[11.5px] text-ink-300">Below the minimum is noted on the enquiry; the order size we like raises its priority.</p>
        <div className="space-y-2">
          <p className="font-semibold text-ink-700">Checklist</p>
          {items.map((it, i) => (
            <div key={i} className="rounded-md border border-line p-2 space-y-1.5">
              <div className="grid gap-2 sm:grid-cols-[150px_1fr_auto_auto] items-center">
                <Input value={it.key} onChange={(e) => setItem(i, { key: e.target.value })} placeholder="key" aria-label="Key" className="font-mono" />
                <Input value={it.label} onChange={(e) => setItem(i, { label: e.target.value })} placeholder="Name" aria-label="Name" />
                <label className="flex items-center gap-1.5 whitespace-nowrap"><input type="checkbox" checked={it.required} onChange={(e) => setItem(i, { required: e.target.checked })} /> Ask for it</label>
                <button type="button" onClick={() => setItems(items.filter((_, j) => j !== i))} className="text-[12px] text-red-500">Remove</button>
              </div>
              <Input value={it.questions.en ?? ""} onChange={(e) => setQ(i, "en", e.target.value)} placeholder="Question in English" aria-label="English question" />
              <div className="grid gap-2 sm:grid-cols-2">
                <Input value={it.questions.hi ?? ""} onChange={(e) => setQ(i, "hi", e.target.value)} placeholder="Question in Hindi (optional)" aria-label="Hindi question" />
                <Input value={it.questions.te ?? ""} onChange={(e) => setQ(i, "te", e.target.value)} placeholder="Question in Telugu (optional)" aria-label="Telugu question" />
              </div>
            </div>
          ))}
          {items.length < 15 && (
            <button type="button" className="text-[12.5px] text-teal-600 hover:underline"
              onClick={() => setItems([...items, { key: "", label: "", required: true, questions: { en: "" } }])}>+ Add a detail</button>
          )}
        </div>
        {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
        <div className="flex gap-2">
          <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
          <Button size="sm" variant="secondary" onClick={onDone}>Cancel</Button>
        </div>
      </div>
    </Card>
  )
}
