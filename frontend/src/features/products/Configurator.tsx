import { useState } from "react"
import { Button } from "@/components/ui/Button"
import { Input } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { cn } from "@/lib/utils"
import { useGuided } from "./hooks"
import type { GuidedAttribute } from "./types"

/** The guided configurator (F3.1): only choices that still lead to a valid product can
 *  be picked; the others are greyed out, and tapping one says which rule rules it out.
 *  A finished product shows its variant code and can be used for the line. */
export function Configurator({ familyId, initial, onUse, onCancel, busy, error }: {
  familyId: string
  initial?: Record<string, string>
  onUse: (values: Record<string, string>) => void
  onCancel: () => void
  busy?: boolean
  error?: string | null
}) {
  const [values, setValues] = useState<Record<string, string>>(initial ?? {})
  const [why, setWhy] = useState<{ key: string; text: string } | null>(null)
  const { data, isLoading, error: loadError, isFetching } = useGuided(familyId, values)
  const pick = (key: string, value: string | null) => {
    setWhy(null)
    const next = { ...values }
    if (value === null || value === "") delete next[key]
    else next[key] = value
    setValues(next)
  }
  if (isLoading) return <Loading />
  if (loadError || !data) return <ErrorText error={loadError} fallback="Could not load the options" />
  const missing = data.attributes.filter((a) => a.required && a.chosen === null).map((a) => a.label.toLowerCase())
  return (
    <div className="space-y-3 rounded-md border border-teal-500 bg-surface-0 p-3 text-[13px]">
      <p className="text-ink-500">Configure as <span className="font-semibold text-ink-900">{data.family.name}</span> <span className="text-ink-300">(options version {data.version})</span></p>
      {data.attributes.map((a) => (
        <div key={a.key} className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-ink-700">{a.label}</span>
            {!a.required && <span className="text-[11.5px] text-ink-300">optional</span>}
            {a.chosen !== null && <button type="button" className="text-[11.5px] text-teal-600 hover:underline" onClick={() => pick(a.key, null)}>clear</button>}
          </div>
          {a.kind === "choice" ? <Choices a={a} onPick={(v) => pick(a.key, v)} onWhy={(text) => setWhy({ key: a.key, text })} />
            : <NumberPick key={`${a.key}:${a.chosen ?? ""}`} a={a} onPick={(v) => pick(a.key, v)} />}
          {why?.key === a.key && <p role="note" className="text-[12.5px] text-amber-600">Not available: {why.text}</p>}
        </div>
      ))}
      {data.problems.length > 0 && (
        <ul role="alert" className="list-disc pl-5 text-[12.5px] text-red-600">{data.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
      )}
      <div className={cn("rounded-md p-2", data.valid ? "bg-teal-100" : "bg-surface-2")}>
        {data.valid && data.result
          ? <p>Variant code <span className="font-mono text-[14px] font-semibold text-ink-900">{data.result.variant_code}</span></p>
          : <p className="text-ink-500">{missing.length ? `Still to pick: ${missing.join(", ")}` : "Change a choice to make a valid product"}</p>}
      </div>
      {error && <p role="alert" className="text-red-500 text-[12.5px]">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!data.valid || busy || isFetching} onClick={() => onUse(values)}>{busy ? "Saving…" : "Use this configuration"}</Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

function Choices({ a, onPick, onWhy }: { a: GuidedAttribute; onPick: (v: string) => void; onWhy: (why: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {(a.options ?? []).map((o) => {
        const on = a.chosen === o.value
        return (
          <button key={o.value} type="button" aria-pressed={on} aria-disabled={!o.available} title={o.available ? o.label : o.why}
            onClick={() => (o.available ? onPick(o.value) : onWhy(o.why ?? "a rule rules it out"))}
            className={cn("rounded-full border px-3 py-1 text-[12.5px] transition-colors",
              on && o.available && "border-teal-500 bg-teal-500 text-white",
              on && !o.available && "border-red-300 bg-red-50 text-red-600",
              !on && o.available && "border-line text-ink-700 hover:border-teal-500",
              !on && !o.available && "border-dashed border-line text-ink-300 line-through")}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

function NumberPick({ a, onPick }: { a: GuidedAttribute; onPick: (v: string) => void }) {
  const [text, setText] = useState(a.chosen ?? "")
  const blocked = (a.ranges ?? []).filter((r) => !r.available)
  const unit = a.unit ? ` ${a.unit}` : ""
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Input className="w-32" inputMode="decimal" value={text} aria-label={a.label}
          placeholder={`${a.min} to ${a.max}`} onChange={(e) => setText(e.target.value)}
          onBlur={() => text !== (a.chosen ?? "") && onPick(text.trim())}
          onKeyDown={(e) => { if (e.key === "Enter") onPick(text.trim()) }} />
        <span className="text-ink-500">{a.unit} · from {a.min} to {a.max}, steps of {a.step}</span>
      </div>
      {blocked.length > 0 && (
        <ul className="text-[12px] text-ink-500">
          {blocked.map((r, i) => (
            <li key={i}>Not available {r.min === r.max ? `at ${r.min}${unit}` : `from ${r.min} to ${r.max}${unit}`}: {r.why}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
