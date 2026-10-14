import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Field, Input, Select } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { useDebounced } from "@/lib/useDebounced"
import { cn, formatDateTime } from "@/lib/utils"
import { useFamilyOptions } from "./hooks"
import { codeExample, codeOf, keyOf, RULE_KINDS, ruleText } from "./labels"
import type { AttributeDef, Cond, OptionDef, OptionsModel, RuleDef, RuleKind } from "./types"

const blankModel: OptionsModel = { attributes: [], rules: [], code_pattern: "" }

function copy(m: OptionsModel): OptionsModel {
  return JSON.parse(JSON.stringify({ attributes: m.attributes, rules: m.rules, code_pattern: m.code_pattern }))
}

/** A family's options and rules (F3.1, C4 A): what a customer can pick, the rules
 *  between the picks (needs, rules out, only when), and the variant code pattern.
 *  Every change is checked as it is typed: rules that conflict, or that leave an option
 *  impossible, are listed and the save is refused until they are fixed. Each save is a
 *  new version; earlier configurations keep the version they were made with. */
export function OptionsEditor({ familyId, canManage }: { familyId: string; canManage: boolean }) {
  const qc = useQueryClient()
  const { data, isLoading, error } = useFamilyOptions(familyId)
  const [draft, setDraft] = useState<OptionsModel | null>(null)
  const [showVersions, setShowVersions] = useState(false)
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["products"] })
    qc.invalidateQueries({ queryKey: ["enquiry"] })
  }
  const sample = useMutation({
    mutationFn: async () => api.post(`/products/families/${familyId}/sample`),
    onSuccess: refresh,
  })
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the options" />
  const cur = data.options
  if (draft) return <EditForm familyId={familyId} start={draft} onDone={() => setDraft(null)} onSaved={refresh} />
  return (
    <div className="space-y-3 text-[13px]">
      {!cur && (
        <div className="space-y-2">
          <p className="text-ink-500">No options yet: the configurator is not offered for {data.family.name}.</p>
          {canManage && (
            <div className="flex flex-wrap gap-2">
              {data.samples.includes("flanges") && (
                <Button size="sm" variant="secondary" disabled={sample.isPending} onClick={() => sample.mutate()}>Start from the flanges sample</Button>
              )}
              <Button size="sm" variant="secondary" onClick={() => setDraft(copy(blankModel))}>Start empty</Button>
            </div>
          )}
          {sample.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(sample.error, "Could not add the sample")}</p>}
        </div>
      )}
      {cur && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Tag variant="teal">Version {cur.version}</Tag>
            <span className="text-ink-500">{cur.note ?? ""}</span>
            <span className="flex-1" />
            {canManage && <Button size="sm" variant="ghost" onClick={() => setDraft(copy(cur))}>Edit options and rules</Button>}
          </div>
          <div className="space-y-1.5">
            {cur.attributes.map((a) => (
              <div key={a.key} className="flex flex-col gap-1 sm:flex-row sm:gap-2">
                <span className="w-40 shrink-0 text-ink-700">{a.label}{a.required ? "" : " (optional)"}</span>
                <span className="text-ink-500">
                  {a.kind === "choice"
                    ? (a.options ?? []).map((o) => o.label).join(" · ")
                    : `a number from ${a.min} to ${a.max}${a.unit ? ` ${a.unit}` : ""}, in steps of ${a.step}`}
                </span>
              </div>
            ))}
          </div>
          {cur.rules.length > 0 && (
            <div className="space-y-1">
              <p className="font-semibold text-ink-700">Rules</p>
              <ul className="space-y-1">
                {cur.rules.map((r) => (
                  <li key={r.id} className="flex gap-2">
                    <span className="w-12 shrink-0 font-mono text-[12px] text-ink-300">{r.id}</span>
                    <span><span className="text-ink-900">{ruleText(r, cur.attributes)}.</span> <span className="text-ink-500">Shown as: “{r.message}”</span></span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-ink-500"><span className="text-ink-700">Variant code:</span> <span className="font-mono">{cur.code_pattern}</span>, for example <span className="font-mono text-ink-900">{codeExample(cur)}</span></p>
          {data.versions.length > 1 && (
            <div>
              <button type="button" className="text-[12.5px] text-teal-600 hover:underline" onClick={() => setShowVersions(!showVersions)}>
                {showVersions ? "Hide" : "Show"} earlier versions ({data.versions.length - 1})
              </button>
              {showVersions && (
                <ul className="mt-1 space-y-0.5 text-[12.5px] text-ink-500">
                  {data.versions.map((v) => (
                    <li key={v.version}>Version {v.version} · {formatDateTime(v.created_at)}{v.note ? ` · ${v.note}` : ""}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function EditForm({ familyId, start, onDone, onSaved }: {
  familyId: string; start: OptionsModel; onDone: () => void; onSaved: () => void
}) {
  const [m, setM] = useState<OptionsModel>(start)
  const [note, setNote] = useState("")
  // Keys of attributes added here follow their label until the key is typed.
  const [fresh, setFresh] = useState<Set<number>>(new Set())
  const checked = useDebounced(m, 500)
  const check = useQuery({
    queryKey: ["products", "check", familyId, checked],
    queryFn: async () => (await api.post<{ problems: string[] }>(`/products/families/${familyId}/check`, checked)).data,
    staleTime: Infinity,
  })
  const save = useMutation({
    mutationFn: async () => api.put(`/products/families/${familyId}/options`, { ...m, note: note.trim() || null }),
    onSuccess: () => { onSaved(); onDone() },
  })
  const attrs = m.attributes
  const setAttr = (i: number, patch: Partial<AttributeDef>) =>
    setM({ ...m, attributes: attrs.map((a, j) => (j === i ? { ...a, ...patch } : a)) })
  const setRule = (i: number, r: RuleDef) => setM({ ...m, rules: m.rules.map((x, j) => (j === i ? r : x)) })
  const addAttr = () => {
    setFresh(new Set([...fresh, attrs.length]))
    setM({ ...m, attributes: [...attrs, { key: "", label: "", kind: "choice", required: true, options: [] }] })
  }
  const removeAttr = (i: number) => {
    const key = attrs[i].key
    setM({
      attributes: attrs.filter((_, j) => j !== i),
      rules: m.rules.filter((r) => r.then.attr !== key && !r.when.some((c) => c.attr === key)),
      code_pattern: m.code_pattern.replace(`{${key}}`, ""),
    })
  }
  const addRule = () => {
    const a = attrs[0]?.key ?? ""
    const b = attrs[1]?.key ?? a
    setM({ ...m, rules: [...m.rules, { kind: "requires", when: [{ attr: a, in: [] }], then: { attr: b, in: [] }, message: "" }] })
  }
  const problems = save.error ? errorProblems(save.error) : (check.data?.problems ?? [])
  const stale = check.isFetching || checked !== m
  return (
    <div className="space-y-4 text-[13px]">
      <section className="space-y-2">
        <p className="font-semibold text-ink-900">What the customer picks</p>
        {attrs.map((a, i) => (
          <div key={i} className="rounded-md border border-line p-2.5 space-y-2">
            <div className="grid gap-2 sm:grid-cols-[1fr_150px_170px_auto] items-end">
              <Field label="Name">
                <Input value={a.label} maxLength={60} placeholder="e.g. Pressure class" onChange={(e) =>
                  setAttr(i, fresh.has(i) ? { label: e.target.value, key: keyOf(e.target.value) } : { label: e.target.value })} />
              </Field>
              <Field label="Key" hint="Used in the code pattern">
                <Input className="font-mono" value={a.key} maxLength={30} onChange={(e) => {
                  setFresh(new Set([...fresh].filter((x) => x !== i)))
                  setAttr(i, { key: e.target.value })
                }} />
              </Field>
              <Field label="Kind">
                <Select value={a.kind} onChange={(e) => setAttr(i, e.target.value === "number"
                  ? { kind: "number", options: undefined, min: "", max: "", step: "1", unit: "" }
                  : { kind: "choice", options: [], min: undefined, max: undefined, step: undefined, unit: undefined })}>
                  <option value="choice">Options to pick</option>
                  <option value="number">A number</option>
                </Select>
              </Field>
              <div className="flex items-center gap-3 pb-2">
                <label className="flex items-center gap-1.5 whitespace-nowrap"><input type="checkbox" checked={a.required} onChange={(e) => setAttr(i, { required: e.target.checked })} /> Must pick</label>
                <button type="button" className="text-[12px] text-red-500" onClick={() => removeAttr(i)}>Remove</button>
              </div>
            </div>
            {a.kind === "choice"
              ? <OptionRows options={a.options ?? []} onChange={(options) => setAttr(i, { options })} />
              : (
                <div className="grid gap-2 grid-cols-2 sm:grid-cols-4">
                  <Field label="From"><Input inputMode="decimal" value={String(a.min ?? "")} onChange={(e) => setAttr(i, { min: e.target.value })} /></Field>
                  <Field label="To"><Input inputMode="decimal" value={String(a.max ?? "")} onChange={(e) => setAttr(i, { max: e.target.value })} /></Field>
                  <Field label="In steps of"><Input inputMode="decimal" value={String(a.step ?? "")} onChange={(e) => setAttr(i, { step: e.target.value })} /></Field>
                  <Field label="Unit"><Input value={a.unit ?? ""} maxLength={20} placeholder="mm" onChange={(e) => setAttr(i, { unit: e.target.value })} /></Field>
                </div>
              )}
          </div>
        ))}
        {attrs.length < 30 && <button type="button" className="text-[12.5px] text-teal-600 hover:underline" onClick={addAttr}>+ Add something to pick</button>}
      </section>

      <section className="space-y-2">
        <p className="font-semibold text-ink-900">Rules</p>
        {m.rules.length === 0 && <p className="text-ink-500">No rules: every combination is allowed.</p>}
        {m.rules.map((r, i) => (
          <RuleRow key={i} r={r} attrs={attrs} onChange={(x) => setRule(i, x)} onRemove={() => setM({ ...m, rules: m.rules.filter((_, j) => j !== i) })} />
        ))}
        {attrs.length > 1 && m.rules.length < 300 && <button type="button" className="text-[12.5px] text-teal-600 hover:underline" onClick={addRule}>+ Add a rule</button>}
      </section>

      <section className="grid gap-2 sm:grid-cols-2">
        <Field label="Variant code pattern" hint={<>Put each key in braces, e.g. FL-{"{size}"}-{"{class}"}. Every attribute must be in it.</>}>
          <Input className="font-mono" value={m.code_pattern} maxLength={100} onChange={(e) => setM({ ...m, code_pattern: e.target.value })} />
        </Field>
        <div className="space-y-1.5">
          <span className="block text-[12px] font-semibold text-ink-700">Example</span>
          <p className="font-mono text-ink-900 py-2">{codeExample(m) || "–"}</p>
          {attrs.length > 0 && (
            <button type="button" className="text-[12px] text-teal-600 hover:underline"
              onClick={() => setM({ ...m, code_pattern: attrs.map((a) => `{${a.key}}`).join("-") })}>Use every key, in order</button>
          )}
        </div>
      </section>

      <Field label="What changed (optional)"><Input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Added Class 2500" /></Field>

      <div role="status" className={cn("rounded-md border p-2.5 space-y-1", problems.length ? "border-red-200 bg-red-50" : "border-line bg-surface-0")}>
        {problems.length === 0
          ? <p className="text-ink-500">{stale ? "Checking the rules…" : "Checked: no conflicts and no dead ends."}</p>
          : (
            <>
              <p className="font-semibold text-red-600">{problems.length === 1 ? "1 thing to fix" : `${problems.length} things to fix`} before saving</p>
              <ul className="list-disc pl-5 space-y-0.5 text-ink-700">{problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
            </>
          )}
      </div>
      {save.error && problems.length === 0 && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending || (!stale && problems.length > 0 && !save.error)}>
          {save.isPending ? "Saving…" : "Save as a new version"}
        </Button>
        <Button size="sm" variant="secondary" onClick={onDone}>Cancel</Button>
      </div>
    </div>
  )
}

function OptionRows({ options, onChange }: { options: OptionDef[]; onChange: (o: OptionDef[]) => void }) {
  const set = (i: number, patch: Partial<OptionDef>) => onChange(options.map((o, j) => (j === i ? { ...o, ...patch } : o)))
  return (
    <div className="space-y-1.5">
      {options.map((o, i) => (
        <div key={i} className="grid gap-1.5 grid-cols-[1fr_90px_auto] sm:grid-cols-[1fr_110px_150px_auto] items-center">
          <Input value={o.label} maxLength={80} placeholder="Option, e.g. Raised face" aria-label="Option" onChange={(e) =>
            set(i, { label: e.target.value, ...(o.value === codeOf(o.label) || !o.value ? { value: codeOf(e.target.value), code: codeOf(e.target.value) } : {}) })} />
          <Input className="font-mono" value={o.code} maxLength={20} placeholder="Code" aria-label="Code" title="Goes into the variant code"
            onChange={(e) => set(i, { code: e.target.value, ...(o.value === o.code ? { value: e.target.value } : {}) })} />
          <Input className="hidden sm:block font-mono" value={o.erp_item ?? ""} maxLength={140} placeholder="ERP item (optional)" aria-label="ERP item"
            title="The ERPNext item this option uses: when it changes, configurations with it are marked for repricing"
            onChange={(e) => set(i, { erp_item: e.target.value || undefined })} />
          <button type="button" className="text-[12px] text-red-500 px-1" aria-label={`Remove ${o.label}`} onClick={() => onChange(options.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      {options.length < 100 && (
        <button type="button" className="text-[12px] text-teal-600 hover:underline" onClick={() => onChange([...options, { value: "", label: "", code: "" }])}>+ Add an option</button>
      )}
    </div>
  )
}

function RuleRow({ r, attrs, onChange, onRemove }: {
  r: RuleDef; attrs: AttributeDef[]; onChange: (r: RuleDef) => void; onRemove: () => void
}) {
  const setWhen = (i: number, c: Cond) => onChange({ ...r, when: r.when.map((x, j) => (j === i ? c : x)) })
  const used = new Set([r.then.attr, ...r.when.map((c) => c.attr)])
  const spare = attrs.find((a) => !used.has(a.key))
  return (
    <div className="rounded-md border border-line p-2.5 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {r.id && <span className="font-mono text-[12px] text-ink-300">{r.id}</span>}
        <span className="text-ink-900">{ruleText(r, attrs)}</span>
        <span className="flex-1" />
        <button type="button" className="text-[12px] text-red-500" onClick={onRemove}>Remove</button>
      </div>
      <div className="space-y-1.5">
        {r.when.map((c, i) => (
          <div key={i} className="flex flex-wrap items-start gap-2">
            <span className="w-14 pt-2 text-ink-500">{i === 0 ? "When" : "and"}</span>
            <CondEditor c={c} attrs={attrs} onChange={(x) => setWhen(i, x)} />
            {r.when.length > 1 && <button type="button" className="pt-2 text-[12px] text-red-500" onClick={() => onChange({ ...r, when: r.when.filter((_, j) => j !== i) })}>✕</button>}
          </div>
        ))}
        {spare && r.when.length < 5 && (
          <button type="button" className="ml-16 text-[12px] text-teal-600 hover:underline"
            onClick={() => onChange({ ...r, when: [...r.when, blankCond(spare)] })}>+ and another condition</button>
        )}
        <div className="flex flex-wrap items-start gap-2">
          <Select className="w-auto" aria-label="Rule kind" value={r.kind} title={RULE_KINDS[r.kind].hint}
            onChange={(e) => onChange({ ...r, kind: e.target.value as RuleKind })}>
            {(Object.keys(RULE_KINDS) as RuleKind[]).map((k) => <option key={k} value={k}>{k === "only_when" ? "allow only then" : `it ${RULE_KINDS[k].label}`}</option>)}
          </Select>
          <CondEditor c={r.then} attrs={attrs} onChange={(then) => onChange({ ...r, then })} />
        </div>
        <Input value={r.message} maxLength={200} placeholder="What a person is told, e.g. Class 900 and above need a ring-type joint face"
          aria-label="Message" onChange={(e) => onChange({ ...r, message: e.target.value })} />
      </div>
    </div>
  )
}

function blankCond(a: AttributeDef): Cond {
  return a.kind === "number" ? { attr: a.key } : { attr: a.key, in: [] }
}

function CondEditor({ c, attrs, onChange }: { c: Cond; attrs: AttributeDef[]; onChange: (c: Cond) => void }) {
  const a = attrs.find((x) => x.key === c.attr)
  return (
    <div className="flex-1 min-w-[220px] space-y-1.5">
      <Select aria-label="Attribute" value={c.attr} onChange={(e) => {
        const next = attrs.find((x) => x.key === e.target.value)
        if (next) onChange(blankCond(next))
      }}>
        {attrs.map((x) => <option key={x.key} value={x.key}>{x.label || x.key}</option>)}
      </Select>
      {a?.kind === "choice" && (
        <div className="flex flex-wrap gap-1">
          {(a.options ?? []).map((o) => {
            const on = (c.in ?? []).includes(o.value)
            return (
              <button key={o.value} type="button" aria-pressed={on}
                onClick={() => onChange({ ...c, in: on ? (c.in ?? []).filter((v) => v !== o.value) : [...(c.in ?? []), o.value] })}
                className={cn("rounded-full border px-2.5 py-0.5 text-[12px]", on ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500")}>
                {o.label || o.value}
              </button>
            )
          })}
        </div>
      )}
      {a?.kind === "number" && (
        <div className="flex items-center gap-2">
          <Input className="w-24" inputMode="decimal" placeholder="from" aria-label="From" value={String(c.min ?? "")}
            onChange={(e) => onChange({ ...c, min: e.target.value === "" ? undefined : e.target.value })} />
          <span className="text-ink-500">to</span>
          <Input className="w-24" inputMode="decimal" placeholder="to" aria-label="To" value={String(c.max ?? "")}
            onChange={(e) => onChange({ ...c, max: e.target.value === "" ? undefined : e.target.value })} />
          <span className="text-ink-500">{a.unit}</span>
        </div>
      )}
    </div>
  )
}
