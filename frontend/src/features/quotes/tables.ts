import type { CostTable } from "./types"

/** Lookup tables in cost models (K8 A), pasted from Excel. Excel copies cells as lines
 *  of tab-separated values. Two layouts are read:
 *
 *  list  one row per entry: the key columns, then the value
 *        150   300   19.1
 *  grid  two keys, the second across the top (only for two keys)
 *        size  150   300   600
 *        15    0.9   0.9   0.9
 */
export type Layout = "list" | "grid"

function cells(line: string): string[] {
  const parts = line.includes("\t") ? line.split("\t") : line.split(",")
  return parts.map((c) => c.trim())
}

/** "1,23,456.50", "₹ 95" -> "123456.50", "95"; null when it is not a number. */
export function numberText(raw: string): string | null {
  const t = raw.replace(/[₹,\s]/g, "")
  return t !== "" && Number.isFinite(Number(t)) ? t : null
}

function keyText(raw: string): string {
  const t = raw.trim()
  const n = numberText(t)
  return n !== null && t.replace(/[,\s]/g, "") === n ? String(Number(n)) : t
}

export function parsePasted(text: string, keys: string[], layout: Layout): { table: CostTable; problems: string[] } {
  const problems: string[] = []
  const rows: CostTable["rows"] = []
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "")
  if (layout === "grid" && keys.length !== 2) {
    return { table: { keys, rows }, problems: ["The grid layout is for tables with two keys"] }
  }
  if (layout === "grid") {
    const [head, ...body] = lines
    if (!head) return { table: { keys, rows }, problems }
    const across = cells(head).slice(1).map(keyText)
    body.forEach((line, i) => {
      const c = cells(line)
      const down = keyText(c[0] ?? "")
      if (!down) { problems.push(`Row ${i + 2}: the first cell needs the ${keys[0]}`); return }
      c.slice(1).forEach((v, j) => {
        if (v === "" || j >= across.length) return
        const n = numberText(v)
        if (n === null) problems.push(`Row ${i + 2}, ${keys[1]} ${across[j]}: ${JSON.stringify(v)} is not a number`)
        else rows.push({ key: [down, across[j]], value: n })
      })
    })
    return { table: { keys, rows }, problems }
  }
  lines.forEach((line, i) => {
    const c = cells(line)
    if (c.length !== keys.length + 1) {
      problems.push(`Row ${i + 1}: needs ${keys.length + 1} cells (${[...keys, "value"].join(", ")})`)
      return
    }
    const n = numberText(c[keys.length])
    if (n === null) {
      if (i === 0) return // a header row
      problems.push(`Row ${i + 1}: ${JSON.stringify(c[keys.length])} is not a number`)
      return
    }
    rows.push({ key: c.slice(0, keys.length).map(keyText), value: n })
  })
  return { table: { keys, rows }, problems }
}

/** The table as text to edit or copy back to Excel, in the layout asked for. */
export function tableText(t: CostTable, layout: Layout): string {
  if (layout === "grid" && t.keys.length === 2) {
    const down: string[] = []
    const across: string[] = []
    const at = new Map<string, string>()
    for (const r of t.rows) {
      if (!down.includes(r.key[0])) down.push(r.key[0])
      if (!across.includes(r.key[1])) across.push(r.key[1])
      at.set(`${r.key[0]}\u0000${r.key[1]}`, r.value)
    }
    return [[`${t.keys[0]}\\${t.keys[1]}`, ...across].join("\t"),
      ...down.map((d) => [d, ...across.map((a) => at.get(`${d}\u0000${a}`) ?? "")].join("\t"))].join("\n")
  }
  return [[...t.keys, "value"].join("\t"), ...t.rows.map((r) => [...r.key, r.value].join("\t"))].join("\n")
}
