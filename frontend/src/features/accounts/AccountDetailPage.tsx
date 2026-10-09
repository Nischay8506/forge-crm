import { Fragment, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Input } from "@/components/ui/Field"
import { Nameplate } from "@/components/ui/Nameplate"
import { ScoreBadge } from "@/components/ui/ScoreBadge"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { formatDate, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { KIND_LABELS, SITE_TYPE_LABELS, SOURCE_LABELS, fieldLabel } from "@/features/steward/labels"
import type { Account, AccountDetail, Provenance } from "@/features/steward/types"
import { TakeoverPanel } from "@/features/agents/TakeoverPanel"
import { AccountErpCard } from "@/features/integrations/AccountErpCard"
import { EraseContact } from "@/features/audit/EraseContact"
import { LegalHoldCard } from "@/features/audit/LegalHoldCard"
import { MessagesCard } from "@/features/audit/MessagesCard"
import { HygieneBreakdown } from "./HygieneBreakdown"

const SHOWN_FIELDS: (keyof Account)[] = [
  "legal_name", "gstin", "pan", "cin", "website", "email", "phone", "address_line1",
  "address_line2", "city", "state", "pincode", "country", "industry",
]

export function AccountDetailPage() {
  const { id } = useParams()
  const perms = usePermissions()
  const canWrite = hasPermission(perms, "accounts.write")
  const canReview = hasPermission(perms, "steward.review")
  const canSteward = hasPermission(perms, "steward.read")
  const canAudit = hasPermission(perms, "audit.read")
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ["account", id],
    queryFn: async () => (await api.get<AccountDetail>(`/accounts/${id}`)).data,
  })
  const decide = useMutation({
    mutationFn: async ({ p, decision }: { p: Provenance; decision: "accept" | "reject" }) =>
      api.post(`/steward/proposals/${p.id}/${decision}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["account", id] }),
  })

  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the account" />
  const a = data.account
  const prov = Object.fromEntries(data.provenance.map((p) => [p.field, p]))
  const active = a.status === "active"

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-ink-900">{a.name}</h1>
            <ScoreBadge score={a.hygiene_score} />
          </div>
          <div className="flex items-center gap-2">
            <Tag>{a.kind === "site" && a.site_type ? SITE_TYPE_LABELS[a.site_type] ?? a.site_type : KIND_LABELS[a.kind]}</Tag>
            {a.tier && a.tier !== "standard" && <Tag variant="gold">{a.tier} account</Tag>}
            {!active && <Tag variant="amber">{a.status}</Tag>}
            {a.gstin && <Nameplate id={a.gstin} />}
            {a.parent_id && <Link to={`/accounts/${a.parent_id}`} className="text-[12px] text-teal-600 hover:underline">Parent record</Link>}
            {canAudit && <Link to={`/agents/runs?record=${a.id}`} className="text-[12px] text-teal-600 hover:underline">What agents did here</Link>}
          </div>
        </div>
        {canWrite && active && (
          <div className="flex gap-2 shrink-0">
            {a.kind === "entity" && (
              <Link to={`/accounts/new?kind=site&parent=${a.id}`}><Button variant="secondary">Add site</Button></Link>
            )}
            <Link to={`/accounts/${a.id}/edit`}><Button variant="secondary">Edit</Button></Link>
          </div>
        )}
      </div>

      {active && <TakeoverPanel recordType="account" recordId={a.id} />}
      {a.status === "merged" && a.merged_into_id && (
        <div className="rounded-md bg-amber-100 text-amber-500 text-[13px] p-3">
          This record was merged. <Link to={`/accounts/${a.merged_into_id}`} className="underline font-semibold">Open the surviving record</Link>
        </div>
      )}
      {data.open_candidates > 0 && canSteward && (
        <div className="rounded-md bg-gold-100 text-ink-700 text-[13px] p-3 flex items-center justify-between">
          <span>{data.open_candidates} possible duplicate(s) of this record are waiting for a decision.</span>
          <Link to="/steward/review" className="font-semibold text-ink-900 underline">Open review queue</Link>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-4 items-start">
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Details</CardTitle></CardHeader>
            <CardContent>
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left text-ink-500 border-b border-line">
                    <th className="py-2 pr-3 font-semibold w-[150px]">Field</th>
                    <th className="py-2 pr-3 font-semibold">Value</th>
                    <th className="py-2 pr-3 font-semibold">Source</th>
                    <th className="py-2 font-semibold">Verified</th>
                  </tr>
                </thead>
                <tbody>
                  {SHOWN_FIELDS.map((f) => {
                    const v = a[f] as string | null
                    const p = prov[f]
                    return (
                      <tr key={f} className="border-b border-line last:border-0">
                        <td className="py-2 pr-3 text-ink-500">{fieldLabel(f)}</td>
                        <td className={v ? "py-2 pr-3" : "py-2 pr-3 text-ink-300"}>{v || "–"}</td>
                        <td className="py-2 pr-3 text-ink-500">{p ? SOURCE_LABELS[p.source] ?? p.source : ""}</td>
                        <td className="py-2 text-ink-500">{p?.verified_at ? formatDate(p.verified_at) : ""}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>

          <AccountErpCard accountId={a.id} isEntity={a.kind === "entity" && active} />

          {data.proposals.length > 0 && (
            <Card>
              <CardHeader><CardTitle>Suggested changes</CardTitle></CardHeader>
              <CardContent className="space-y-2">
                <p className="text-[12px] text-ink-500">
                  These values came from a less trusted source than the current ones, so a person decides.
                </p>
                {data.proposals.map((p) => (
                  <div key={p.id} className="flex items-center justify-between gap-3 rounded-md border border-line p-2.5 text-[13px]">
                    <div>
                      <span className="text-ink-500">{fieldLabel(p.field)}:</span>{" "}
                      <span className="line-through text-ink-300">{(a[p.field as keyof Account] as string) ?? "–"}</span>{" "}
                      → <span className="font-semibold">{p.value}</span>
                      <span className="text-ink-300"> · {SOURCE_LABELS[p.source] ?? p.source}{p.source_ref ? ` (${p.source_ref})` : ""}</span>
                    </div>
                    {canReview && (
                      <div className="flex gap-2 shrink-0">
                        <Button size="sm" variant="gold" disabled={decide.isPending}
                          onClick={() => decide.mutate({ p, decision: "accept" })}>Accept</Button>
                        <Button size="sm" variant="secondary" disabled={decide.isPending}
                          onClick={() => decide.mutate({ p, decision: "reject" })}>Reject</Button>
                      </div>
                    )}
                  </div>
                ))}
                <ErrorText error={decide.error} fallback="Could not save the decision" />
              </CardContent>
            </Card>
          )}

          {a.kind !== "site" && (
            <Card>
              <CardHeader><CardTitle>{a.kind === "group" ? "Companies" : "Sites"}</CardTitle></CardHeader>
              <CardContent>
                {data.children.length === 0 ? <Empty>None yet.</Empty> : (
                  <ul className="divide-y divide-line text-[13px]">
                    {data.children.map((c) => (
                      <li key={c.id} className="py-2 flex items-center justify-between">
                        <Link to={`/accounts/${c.id}`} className="text-teal-600 hover:underline">{c.name}</Link>
                        <span className="text-ink-300">
                          {c.site_type ? SITE_TYPE_LABELS[c.site_type] ?? c.site_type : KIND_LABELS[c.kind]}
                          {c.city ? ` · ${c.city}` : ""}{c.gstin ? ` · ${c.gstin}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          )}

          <ContactsCard accountId={a.id} contacts={data.contacts} canWrite={canWrite && active} />
          <MessagesCard accountId={a.id} contacts={data.contacts} />
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Data hygiene</CardTitle></CardHeader>
            <CardContent><HygieneBreakdown details={a.hygiene_details} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Merge history</CardTitle></CardHeader>
            <CardContent>
              {data.merges.length === 0 ? <Empty>No merges.</Empty> : (
                <ul className="space-y-2 text-[12.5px]">
                  {data.merges.map((m) => (
                    <li key={m.id} className="space-y-0.5">
                      <div className="flex items-center justify-between">
                        <Link to={`/steward/merges/${m.id}`} className="text-teal-600 hover:underline">
                          {m.survivor_id === a.id ? "Absorbed another record" : "Merged into another record"}
                        </Link>
                        {m.undone_at && <Tag variant="amber">Undone</Tag>}
                      </div>
                      <div className="text-ink-300 flex items-center gap-1.5">
                        <Actor actor={m.decided_by} /> · {formatDateTime(m.created_at)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
          <LegalHoldCard accountId={a.id} />
          <p className="text-[11.5px] text-ink-300">
            Created {formatDateTime(a.created_at)} from {SOURCE_LABELS[a.source] ?? a.source}
          </p>
        </div>
      </div>
    </div>
  )
}

function ContactsCard({ accountId, contacts, canWrite }: {
  accountId: string; contacts: AccountDetail["contacts"]; canWrite: boolean
}) {
  const qc = useQueryClient()
  const perms = usePermissions()
  const canErase = hasPermission(perms, "privacy.erase")
  const [erasing, setErasing] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", designation: "" })
  const add = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = { account_id: accountId }
      for (const [k, v] of Object.entries(form)) if (v.trim()) body[k] = v.trim()
      return (await api.post("/contacts", body)).data
    },
    onSuccess: () => {
      setForm({ full_name: "", email: "", phone: "", designation: "" })
      setOpen(false)
      qc.invalidateQueries({ queryKey: ["account", accountId] })
    },
  })
  const problems = errorProblems(add.error)
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>Contacts</CardTitle>
        {canWrite && !open && <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>Add contact</Button>}
      </CardHeader>
      <CardContent className="space-y-3">
        {open && (
          <form className="grid grid-cols-2 gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
            <Input placeholder="Full name *" required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            <Input placeholder="Designation" value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })} />
            <Input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            <Input placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            {add.error && (
              <div className="col-span-2 text-red-500 text-[12.5px]">
                {problems.length ? problems.map((p) => <p key={p}>{p}</p>) : errorMessage(add.error, "Could not save")}
              </div>
            )}
            <div className="col-span-2 flex gap-2">
              <Button size="sm" type="submit" disabled={add.isPending}>Save contact</Button>
              <Button size="sm" type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
            </div>
          </form>
        )}
        {contacts.length === 0 ? <Empty>No contacts yet.</Empty> : (
          <table className="w-full text-[13px]">
            <tbody>
              {contacts.map((c) => (
                <Fragment key={c.id}>
                  <tr className="border-b border-line last:border-0">
                    <td className="py-2 pr-3 font-medium">{c.full_name}</td>
                    <td className="py-2 pr-3 text-ink-500">{c.designation ?? ""}</td>
                    <td className="py-2 pr-3">{c.email ?? ""}</td>
                    <td className="py-2 pr-3">{c.phone ?? ""}</td>
                    <td className="py-2 text-right"><ScoreBadge score={c.hygiene_score} /></td>
                    {canErase && (
                      <td className="py-2 pl-2 text-right">
                        <button className="text-[12px] text-ink-300 hover:text-red-500 hover:underline"
                          onClick={() => setErasing(erasing === c.id ? null : c.id)}>Erase…</button>
                      </td>
                    )}
                  </tr>
                  {erasing === c.id && (
                    <tr><td colSpan={6} className="pb-2"><EraseContact contactId={c.id} onClose={() => setErasing(null)} /></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  )
}
