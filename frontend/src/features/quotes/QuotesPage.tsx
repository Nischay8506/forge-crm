import { Link, useSearchParams } from "react-router-dom"
import { Card } from "@/components/ui/Card"
import { Nameplate } from "@/components/ui/Nameplate"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDate } from "@/lib/utils"
import { useQuotes } from "./hooks"
import { STATUS_TONE } from "./labels"

const VIEWS = [
  { key: "open", label: "Open" },
  { key: "mine", label: "Mine" },
  { key: "all", label: "All" },
] as const

/** Every quote: number, customer, enquiry, status, revision and how long it is valid. */
export function QuotesPage() {
  const [search, setSearch] = useSearchParams()
  const view = VIEWS.find((v) => v.key === search.get("view"))?.key ?? "open"
  const { data, isLoading, error } = useQuotes(view === "all" ? "" : "open", view === "mine")
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {VIEWS.map((v) => (
          <button key={v.key} type="button" onClick={() => setSearch(v.key === "open" ? {} : { view: v.key }, { replace: true })}
            className={cn("rounded-full border px-3 py-1 text-[12.5px]",
              v.key === view ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500 hover:text-ink-900")}>
            {v.label}
          </button>
        ))}
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the quotes" />
      {data && data.quotes.length === 0 && (
        <Empty>No quotes yet. Start one from an enquiry with "Start a quote".</Empty>
      )}
      {data && data.quotes.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[640px]">
            <thead>
              <tr className="text-left text-ink-500 border-b border-line">
                <th className="py-2 px-3 font-semibold">Quote</th>
                <th className="py-2 pr-2 font-semibold">Customer</th>
                <th className="py-2 pr-2 font-semibold">Enquiry</th>
                <th className="py-2 pr-2 font-semibold">Status</th>
                <th className="py-2 pr-2 font-semibold">Valid until</th>
                <th className="py-2 pr-3 font-semibold">Owner</th>
              </tr>
            </thead>
            <tbody>
              {data.quotes.map((q) => (
                <tr key={q.id} className="border-b border-line last:border-0 hover:bg-surface-0">
                  <td className="py-2 px-3 whitespace-nowrap">
                    <Link to={`/quotes/${q.id}`} className="hover:underline"><Nameplate id={q.number} /></Link>
                    <span className="ml-1.5 text-[12px] text-ink-500">R{q.revision}</span>
                  </td>
                  <td className="py-2 pr-2 text-ink-900">{q.customer ?? "–"}</td>
                  <td className="py-2 pr-2">
                    {q.enquiry && <Link to={`/enquiries/${q.enquiry.id}`} className="text-teal-600 hover:underline font-mono text-[12px]">{q.enquiry.ref}</Link>}
                  </td>
                  <td className="py-2 pr-2"><Tag variant={STATUS_TONE[q.status]}>{q.status_label}</Tag></td>
                  <td className="py-2 pr-2 whitespace-nowrap text-ink-700">{formatDate(q.valid_until)}</td>
                  <td className="py-2 pr-3 text-ink-700">{q.owner_name ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}
