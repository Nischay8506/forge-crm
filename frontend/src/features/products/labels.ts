import type { AttributeDef, Cond, OptionsModel, RuleDef, RuleKind } from "./types"

export const RULE_KINDS: Record<RuleKind, { label: string; hint: string }> = {
  requires: { label: "needs", hint: "When the conditions hold, the attribute must be one of these" },
  excludes: { label: "rules out", hint: "When the conditions hold, the attribute can't be any of these" },
  only_when: { label: "only when", hint: "These options are allowed only when the conditions hold" },
}

/** "a", "a or b", "a, b or c". */
export function orList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? ""
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`
}

function optionLabels(a: AttributeDef | undefined, values: string[]): string[] {
  return values.map((v) => a?.options?.find((o) => o.value === v)?.label ?? v)
}

function rangeText(c: Cond, unit: string | null | undefined): string {
  const u = unit ? ` ${unit}` : ""
  const has = (v: unknown) => v !== undefined && v !== null && v !== ""
  if (has(c.min) && has(c.max)) return `from ${c.min} to ${c.max}${u}`
  if (has(c.min)) return `${c.min}${u} or more`
  if (has(c.max)) return `up to ${c.max}${u}`
  return "any"
}

/** "pressure class is Class 900 or Class 1500", "length is from 100 to 500 mm". */
export function condText(c: Cond, attrs: AttributeDef[]): string {
  const a = attrs.find((x) => x.key === c.attr)
  const name = (a?.label ?? c.attr).toLowerCase()
  if (a?.kind === "number" || c.in === undefined) return `${name} is ${rangeText(c, a?.unit)}`
  return `${name} is ${orList(optionLabels(a, c.in))}`
}

/** A rule as a sentence, the way the setup screen lists it. */
export function ruleText(r: RuleDef, attrs: AttributeDef[]): string {
  const when = r.when.map((c) => condText(c, attrs)).join(" and ")
  const a = attrs.find((x) => x.key === r.then.attr)
  const name = (a?.label ?? r.then.attr).toLowerCase()
  const what = a?.kind === "number" || r.then.in === undefined
    ? rangeText(r.then, a?.unit) : orList(optionLabels(a, r.then.in))
  switch (r.kind) {
    case "requires": return `When ${when}, ${name} must be ${what}`
    case "excludes": return `When ${when}, ${name} can't be ${what}`
    case "only_when": return `${cap(name)} ${what} only when ${when}`
  }
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** An example variant code: the first option of each attribute, numbers at their
 *  minimum. Empty parts collapse as the backend does (FL--RF becomes FL-RF). */
export function codeExample(m: Pick<OptionsModel, "attributes" | "code_pattern">): string {
  const s = m.code_pattern.replace(/\{([^{}]*)\}/g, (_, key: string) => {
    const a = m.attributes.find((x) => x.key === key)
    if (!a) return ""
    if (a.kind === "choice") return a.options?.[0]?.code ?? ""
    return a.min === undefined || a.min === null ? "" : String(a.min)
  })
  return s.replace(/([-/_.])[-/_.]+/g, "$1").replace(/^[-/_. ]+|[-/_. ]+$/g, "").slice(0, 100)
}

/** A key from a label: "Pressure class" -> "pressure_class". */
export function keyOf(label: string): string {
  const k = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
  return /^[a-z]/.test(k) ? k.slice(0, 30) : `a_${k}`.slice(0, 30)
}

/** A code from an option label: "Raised face" -> "RAISED". Short, upper case. */
export function codeOf(label: string): string {
  const word = label.trim().split(/\s+/)[0] ?? ""
  return word.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 10)
}
