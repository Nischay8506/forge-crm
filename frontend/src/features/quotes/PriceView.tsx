import { PRICE_SOURCE_LABELS, percent, rupees } from "./labels"
import type { PriceWaterfall } from "./types"

/** "Why this price" (F3.3): the list price and where it came from, every discount with
 *  its amount, the margin the price leaves against the family's floor, and anything that
 *  stopped Forge pricing the line (C2 A). */
export function PriceView({ p, unit }: { p: PriceWaterfall; unit?: string | null }) {
  const per = unit || "unit"
  const below = p.status === "below_floor"
  return (
    <div className="rounded-md bg-surface-0 border border-line p-3 text-[12.5px] space-y-2.5">
      <p className="text-ink-700">
        <span className="font-semibold text-ink-900">{PRICE_SOURCE_LABELS[p.source]}.</span>{" "}
        {p.policy
          ? <>Target {percent(p.policy.target_margin_pct)}, floor {percent(p.policy.floor_margin_pct)}, from the {p.policy.scope === "family" ? "family's" : "company"} policy of {p.policy.from}.</>
          : <>No pricing policy is set, so there is no target margin or floor.</>}
      </p>
      {p.problems.length > 0 && (
        <div className={`rounded-md px-2.5 py-2 ${below ? "bg-[#fdeaea] text-[#8a2d2d]" : "bg-[#fdf1de] text-[#8a5300]"}`}>
          <p className="font-semibold">{below ? "Below the floor" : "Not priced"}</p>
          <ul className="list-disc pl-4 space-y-0.5">{p.problems.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
      )}
      {p.steps.length > 0 && (
        <table className="w-full">
          <tbody>
            {p.steps.map((s, i) => (
              <tr key={i} className="align-top border-b border-dashed border-line last:border-0">
                <td className="py-1 pr-2 text-ink-900">{s.label}</td>
                <td className="py-1 pr-2 text-right font-mono whitespace-nowrap text-ink-500">{s.amount ? rupees(s.amount) : ""}</td>
                <td className="py-1 text-right font-mono whitespace-nowrap">{rupees(s.price)}</td>
              </tr>
            ))}
            <tr>
              <td className="pt-1.5 pr-2 font-semibold text-ink-900">Price per {per}</td>
              <td />
              <td className="pt-1.5 text-right font-mono font-semibold whitespace-nowrap">{rupees(p.unit_price)}</td>
            </tr>
            {p.unit_cost && (
              <tr>
                <td className="pr-2 text-ink-500">Cost per {per}, leaving {percent(p.margin_pct)} margin against a {percent(p.floor_margin_pct)} floor</td>
                <td />
                <td className="text-right font-mono whitespace-nowrap text-ink-500">{rupees(p.unit_cost)}</td>
              </tr>
            )}
            {p.amount && (
              <tr>
                <td className="pr-2 text-ink-500">{p.qty} {p.unit ?? ""} on this line</td>
                <td />
                <td className="text-right font-mono whitespace-nowrap text-ink-500">{rupees(p.amount)}</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      {p.approval && (
        <p className="text-ink-700">
          Approved by {p.approval.by}
          {p.approval.unit_price ? <> at {rupees(p.approval.unit_price)}</> : null}.
        </p>
      )}
      {(p.notes ?? []).length > 0 && (
        <ul className="list-disc pl-4 space-y-0.5 text-ink-500">{p.notes.map((t) => <li key={t}>{t}</li>)}</ul>
      )}
      <p className="text-ink-300">Priced on {p.at}{p.tier ? ` · ${p.tier} customer` : ""}</p>
    </div>
  )
}
