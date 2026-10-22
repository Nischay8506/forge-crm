import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { chainLine, discountLine, money, plainPct, type Stage } from "./doaLabels"

type CardOut = {
  id: string
  status: string
  step: number
  of: number
  requested_by: string | null
  created_at: string
  decided_by: string | null
  decided_at: string | null
  note: string | null
  why: string | null
  competitor: string | null
  allowed_pct: string | null
  stale: boolean
}
type DiscountOut = {
  quote_id: string
  number: string
  revision: number
  needed: boolean
  settled: boolean
  no_matrix: boolean
  below_floor: boolean
  discount_pct: string
  amount: string | null
  margin_pct: string | null
  floor_margin_pct: string | null
  given_away: string | null
  list_total: string | null
  discounted_line_count: number
  lines_not_measured: number
  discounted_lines: {
    position: number
    description: string
    qty: string | null
    unit: string | null
    list_price: string | null
    unit_price: string | null
    discount_pct: string
    given_away: string | null
    margin_pct: string | null
    floor_margin_pct: string | null
  }[]
  stages: Stage[]
  problems: string[]
  recommendation: string
  card: CardOut | null
}

const WORDS: Record<string, string> = {
  pending: "Waiting for a decision",
  approved: "Approved",
  rejected: "Sent back",
  countered: "Countered",
  superseded: "Replaced, because the quote changed",
  failed: "Could not be carried out",
}

/** A quote's discount, and who has to sign it off (F3.4, Z5 A).
 *
 *  One panel for the whole quote, not one per discounted line: the discount is the
 *  quote's. It says what the rulebook asks for before anybody is asked, so a rep can see
 *  the chain they are about to start, and then carries the card's state once it is asked.
 */
export function DiscountApproval({ quoteId, revision }: { quoteId: string; revision?: number }) {
  const qc = useQueryClient()
  const mayWork = hasPermission(usePermissions(), "quotes.work")
  const [why, setWhy] = useState("")
  const [competitor, setCompetitor] = useState("")
  const q = useQuery({
    queryKey: ["doa", "quote", quoteId, revision ?? "active"],
    queryFn: async () => (await api.get<DiscountOut>(`/doa/quote/${quoteId}`,
      { params: revision ? { revision } : undefined })).data,
  })
  const ask = useMutation({
    mutationFn: async () => (await api.post<DiscountOut>(`/doa/quote/${quoteId}/approval`, {
      why: why.trim() || null, competitor: competitor.trim() || null, revision: revision ?? null,
    })).data,
    onSuccess: () => {
      setWhy("")
      setCompetitor("")
      qc.invalidateQueries({ queryKey: ["doa", "quote", quoteId] })
      qc.invalidateQueries({ queryKey: ["quote", quoteId] })
      qc.invalidateQueries({ queryKey: ["decisions"] })
    },
  })

  if (q.isLoading) return <Loading />
  if (q.error || !q.data) {
    return <ErrorText error={q.error} fallback="Could not work out this quote's discount" />
  }
  const d = q.data
  if (Number(d.discount_pct) === 0 && !d.needed && d.card === null) return null
  const card = d.card
  const open = card !== null && card.status === "pending" && !card.stale
  const problems = ask.error ? errorProblems(ask.error) : []

  return (
    <Card className="p-4 sm:p-5 space-y-4">
      <div className="flex flex-wrap items-center gap-2 justify-between">
        <h3 className="text-base font-semibold text-ink-900">Discount approval</h3>
        {d.settled ? <Tag variant="green">Approved</Tag>
          : open ? <Tag variant="amber">{`Step ${card.step} of ${card.of}`}</Tag>
            : d.needed ? <Tag variant="gold">Needs approval</Tag>
              : <Tag variant="neutral">No approval needed</Tag>}
      </div>

      <p className="text-sm text-ink-700">{discountLine(d)}</p>
      {d.margin_pct && d.floor_margin_pct && (
        <p className="text-sm text-ink-500">
          {`It leaves ${plainPct(d.margin_pct)}% against a ${plainPct(d.floor_margin_pct)}% floor.`}
          {d.below_floor && " That is under the floor."}
        </p>
      )}
      {d.lines_not_measured > 0 && (
        <p className="text-sm text-ink-500">
          {d.lines_not_measured === 1
            ? "1 line has no list price, so its discount is not counted here."
            : `${d.lines_not_measured} lines have no list price, so their discounts are not counted here.`}
        </p>
      )}

      {d.discounted_lines.length > 0 && (
        <ul className="text-sm text-ink-700 space-y-1">
          {d.discounted_lines.map((ln) => (
            <li key={ln.position} className="flex flex-wrap gap-x-2">
              <span className="text-ink-500">{ln.position}.</span>
              <span className="min-w-0 flex-1 truncate">{ln.description}</span>
              <span className="tabular-nums">{`${plainPct(ln.discount_pct)}% off`}</span>
              {ln.given_away && <span className="tabular-nums text-ink-500">{money(ln.given_away)}</span>}
            </li>
          ))}
        </ul>
      )}

      <div className="text-sm">
        <span className="text-ink-500">Who has to sign it off: </span>
        <span className="text-ink-900">{chainLine(d.stages)}</span>
      </div>
      {d.stages.some((s) => s.people === 0) && (
        <p className="text-sm text-ink-500">
          Nobody is in{" "}
          {d.stages.filter((s) => s.people === 0).map((s) => s.role_name).join(", ")} yet, so a
          quote that reaches that step would wait.
        </p>
      )}
      {d.stages.length > 0 && (
        <ul className="text-sm text-ink-500 space-y-1">
          {d.stages.map((s) => (
            <li key={`${s.role}-${s.because.join("|")}`}>
              {`${s.role_name}: ${s.because.join("; ")}`}
            </li>
          ))}
        </ul>
      )}
      {d.problems.map((p) => (
        <p key={p} className="text-sm text-amber-700">
          {d.no_matrix
            ? "No approval rulebook has been set, so Forge cannot say who should sign this off."
            : p}
        </p>
      ))}
      {d.no_matrix && (
        <Link to="/quotes/approval-rules" className="text-sm text-teal-700 underline">
          Write the approval rules
        </Link>
      )}

      {card && (
        <div className="rounded border border-line bg-surface-2 p-3 text-sm space-y-1">
          <div className="flex flex-wrap items-center gap-2 justify-between">
            <span className="text-ink-900">{WORDS[card.status] ?? card.status}</span>
            <Link to={`/decisions?card=${card.id}`} className="text-teal-700 underline">
              Open the card
            </Link>
          </div>
          <p className="text-ink-500">{`Asked ${formatDateTime(card.created_at)}`}</p>
          {card.why && <p className="text-ink-700">{`The rep's reason: ${card.why}`}</p>}
          {card.competitor && (
            <p className="text-ink-700">{`The rep's competitor claim: ${card.competitor}`}</p>
          )}
          {card.allowed_pct && (
            <p className="text-ink-700">{`Countered to ${plainPct(card.allowed_pct)}%`}</p>
          )}
          {card.decided_at && (
            <p className="text-ink-500">
              {`Decided ${formatDateTime(card.decided_at)}${card.note ? `: ${card.note}` : ""}`}
            </p>
          )}
          {card.stale && card.status !== "superseded" && (
            <p className="text-amber-700">
              The quote changed after this card was asked, so it is about numbers the quote no
              longer has. Ask again.
            </p>
          )}
        </div>
      )}

      {mayWork && d.needed && !open && (
        <div className="space-y-3 border-t border-line pt-4">
          <Field label="Why this discount" hint="The approver sees this as your own words, not as something Forge checked.">
            <Input value={why} onChange={(e) => setWhy(e.target.value)} maxLength={1000}
                   placeholder="Repeat buyer, three orders last year" />
          </Field>
          <Field label="Competitor pressure" hint="Optional, and shown as your claim.">
            <Input value={competitor} onChange={(e) => setCompetitor(e.target.value)}
                   maxLength={300} placeholder="Tata quoted 8% less" />
          </Field>
          {problems.length > 0 && (
            <ul role="alert" className="list-disc pl-5 text-sm text-red-500">
              {problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          <Button onClick={() => ask.mutate()} disabled={ask.isPending}>
            {ask.isPending ? "Sending…" : card ? "Ask again" : "Send for approval"}
          </Button>
          <p className="text-sm text-ink-500">{d.recommendation}</p>
        </div>
      )}
    </Card>
  )
}
