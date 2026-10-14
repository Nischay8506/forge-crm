import { formatDate, formatDateTime } from "@/lib/utils"
import { GROUP_LABELS, SOURCE_LABELS, rupees } from "./labels"
import type { CostBreakdown } from "./types"

/** "Why this cost" (F3.2): where the cost came from, every part with how it was worked
 *  out, what could not be (K10 A), the rates with their dates, the ERPNext BOMs and the
 *  unit conversions (K11, K12 A). Used on the quote and in the cost model's preview. */
export function CostView({ c, unit }: { c: CostBreakdown; unit?: string | null }) {
  const per = unit || "unit"
  return (
    <div className="rounded-md bg-surface-0 border border-line p-3 text-[12.5px] space-y-2.5">
      <p className="text-ink-700">
        <span className="font-semibold text-ink-900">{SOURCE_LABELS[c.source]}.</span> {c.why}
      </p>
      {c.problems.length > 0 && (
        <div className="rounded-md bg-[#fdf1de] px-2.5 py-2 text-[#8a5300]">
          <p className="font-semibold">{c.source === "unknown" ? "Nothing covers this line" : "Not worked out"}</p>
          <ul className="list-disc pl-4 space-y-0.5">{c.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      {c.parts.length > 0 && (
        <table className="w-full">
          <tbody>
            {c.parts.map((p, i) => (
              <tr key={i} className="align-top border-b border-dashed border-line last:border-0">
                <td className="py-1 pr-2 text-ink-500 whitespace-nowrap w-20">{GROUP_LABELS[p.group] ?? p.group}</td>
                <td className="py-1 pr-2 text-ink-900">{p.label}<span className="block text-ink-500 text-[12px]">{p.detail}</span></td>
                <td className="py-1 text-right font-mono whitespace-nowrap">{rupees(p.amount)}</td>
              </tr>
            ))}
            {c.per_unit && (
              <tr>
                <td />
                <td className="pt-1.5 pr-2 font-semibold text-ink-900">Cost per {per}{c.status === "incomplete" ? " (from what is known)" : ""}</td>
                <td className="pt-1.5 text-right font-mono font-semibold whitespace-nowrap">{rupees(c.per_unit)}</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      {c.one_time.length > 0 && (
        <div>
          <p className="font-semibold text-ink-700">One-time, charged once on the quote</p>
          {c.one_time.map((t) => (
            <p key={t.label} className="flex justify-between gap-2"><span className="text-ink-900">{t.label}</span><span className="font-mono">{rupees(t.amount)}</span></p>
          ))}
        </div>
      )}
      {c.review && (
        <p className="text-teal-600">
          Reviewed {formatDateTime(c.review.at)}: {c.review.unit_cost ? `the unit cost is set to ${rupees(c.review.unit_cost)}` : "the cost is used as worked out"}.
        </p>
      )}
      {c.rates.length > 0 && (
        <div className="text-ink-500">
          <p className="font-semibold text-ink-700">Rates used (on {formatDate(c.at)})</p>
          <ul className="space-y-0.5">
            {c.rates.map((r) => (
              <li key={`${r.kind}:${r.code}`}>
                {r.code} {r.name !== r.code ? `(${r.name}) ` : ""}{rupees(r.rate)} per {r.unit === "m3" ? "m³" : r.unit === "m2" ? "m²" : r.unit}
                {r.source === "erpnext" ? " · ERPNext workstation" : r.from ? ` · from ${formatDate(r.from)}` : ""}
                {r.yield_pct ? ` · ${r.yield_pct}% yield` : ""}{r.scrap_rate ? ` · scrap ${rupees(r.scrap_rate)}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.boms.length > 0 && (
        <div className="text-ink-500">
          <p className="font-semibold text-ink-700">ERPNext BOMs used (this version is kept)</p>
          <ul className="space-y-0.5">
            {c.boms.map((b) => (
              <li key={`${b.code}:${b.level}`} style={{ paddingLeft: `${b.level * 12}px` }}>
                <span className="font-mono text-ink-700">{b.code}</span> for {b.quantity} {b.uom ?? ""} of {b.item_code} · changed in ERPNext {formatDateTime(b.modified_at)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {c.conversions.length > 0 && (
        <div className="text-ink-500">
          <p className="font-semibold text-ink-700">Unit conversions</p>
          <ul className="space-y-0.5">
            {c.conversions.map((x, i) => <li key={i}>{x.what}: {x.qty} {x.from} = {x.result} {x.to}</li>)}
          </ul>
        </div>
      )}
      {(Object.keys(c.values).length > 0 || c.packed_kg) && (
        <p className="text-ink-500">
          {Object.entries(c.values).map(([k, v]) => `${k} ${v}`).join(" · ")}
          {c.packed_kg && <>{Object.keys(c.values).length > 0 ? " · " : ""}packed {c.packed_kg} kg{c.packed_m3 ? `, ${c.packed_m3} m³` : ""} per {per}</>}
        </p>
      )}
    </div>
  )
}
