import { useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { ContactPicker, type PickedContact } from "@/features/messages/ContactPicker"
import { useFamilies } from "./hooks"
import { LANGUAGES } from "./labels"
import type { EnquiryDetail } from "./types"

type LineDraft = { description: string; dimensions: string; material: string; qty: string; unit: string }
const blank: LineDraft = { description: "", dimensions: "", material: "", qty: "", unit: "" }

/** An enquiry a person took by phone, at a visit, at a trade fair or anywhere else.
 *  They have already spoken to the customer, so the first response counts as done. */
export function ManualEnquiryPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const families = useFamilies()
  const [contact, setContact] = useState<(PickedContact & { account_id?: string | null }) | null>(null)
  const [customer, setCustomer] = useState({ company: "", contact_name: "", phone: "", email: "", city: "", state: "" })
  const [summary, setSummary] = useState("")
  const [how, setHow] = useState("Phone call")
  const [lines, setLines] = useState<LineDraft[]>([{ ...blank }])
  const [details, setDetails] = useState({ delivery_location: "", target_date: "", special_requirements: "" })
  const [family, setFamily] = useState("")
  const [language, setLanguage] = useState("")
  const [tender, setTender] = useState(false)
  const save = useMutation({
    mutationFn: async () => (await api.post<EnquiryDetail>("/enquiries", {
      contact_id: contact?.id ?? null,
      customer: Object.fromEntries(Object.entries(customer).map(([k, v]) => [k, v.trim() || null])),
      summary: summary.trim() || null, received_how: how || null, is_tender: tender,
      product_family_id: family || null, language: language || null,
      details: { ...Object.fromEntries(Object.entries(details).map(([k, v]) => [k, v.trim() || null])) },
      lines: lines.filter((l) => l.description.trim()).map((l) => ({
        description: l.description.trim(), dimensions: l.dimensions.trim() || null, material: l.material.trim() || null,
        qty: l.qty.trim() ? Number(l.qty) : null, unit: l.unit.trim() || null,
      })),
    })).data,
    onSuccess: (e) => {
      qc.invalidateQueries({ queryKey: ["enquiries"] })
      navigate(`/enquiries/${e.id}`)
    },
  })
  const setLine = (i: number, patch: Partial<LineDraft>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  const problems = errorProblems(save.error)

  return (
    <div className="space-y-4 max-w-[860px]">
      <div className="text-[12.5px]"><Link to="/enquiries" className="text-teal-600 hover:underline">← Enquiries</Link></div>
      <h2 className="text-xl font-bold text-ink-900">Manual enquiry</h2>
      <Card>
        <CardHeader className="pb-2"><CardTitle>Who it is from</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-3">
          <Field label="An existing contact" group hint="Or leave this empty and write the details below">
            <ContactPicker value={contact} onChange={setContact} />
          </Field>
          {!contact && (
            <div className="grid gap-2 sm:grid-cols-3">
              <Field label="Company"><Input value={customer.company} onChange={(e) => setCustomer({ ...customer, company: e.target.value })} /></Field>
              <Field label="Contact"><Input value={customer.contact_name} onChange={(e) => setCustomer({ ...customer, contact_name: e.target.value })} /></Field>
              <Field label="Phone"><Input value={customer.phone} onChange={(e) => setCustomer({ ...customer, phone: e.target.value })} /></Field>
              <Field label="Email"><Input value={customer.email} onChange={(e) => setCustomer({ ...customer, email: e.target.value })} /></Field>
              <Field label="City"><Input value={customer.city} onChange={(e) => setCustomer({ ...customer, city: e.target.value })} /></Field>
              <Field label="State"><Input value={customer.state} onChange={(e) => setCustomer({ ...customer, state: e.target.value })} /></Field>
            </div>
          )}
          <Field label="How it came in">
            <Select value={how} onChange={(e) => setHow(e.target.value)}>
              {["Phone call", "Visit", "Trade fair", "WhatsApp", "Walk-in", "Other"].map((h) => <option key={h}>{h}</option>)}
            </Select>
          </Field>
        </div>
      </Card>
      <Card>
        <CardHeader className="pb-2"><CardTitle>What is asked for</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-2">
          <Field label="Summary"><Input value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={300} placeholder="e.g. MS plates for a new shed" /></Field>
          {lines.map((l, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[2fr_1.4fr_1fr_80px_80px_auto] items-center">
              <Input value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} placeholder="Item" aria-label="Item" />
              <Input value={l.dimensions} onChange={(e) => setLine(i, { dimensions: e.target.value })} placeholder="Size" aria-label="Size" />
              <Input value={l.material} onChange={(e) => setLine(i, { material: e.target.value })} placeholder="Grade" aria-label="Grade" />
              <Input value={l.qty} onChange={(e) => setLine(i, { qty: e.target.value })} placeholder="Qty" inputMode="decimal" aria-label="Quantity" />
              <Input value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })} placeholder="Unit" aria-label="Unit" />
              <button type="button" onClick={() => setLines(lines.length > 1 ? lines.filter((_, j) => j !== i) : [{ ...blank }])} className="text-[12px] text-red-500">Remove</button>
            </div>
          ))}
          <button type="button" className="text-[12.5px] text-teal-600 hover:underline" onClick={() => setLines([...lines, { ...blank }])}>+ Add a line</button>
          <div className="grid gap-2 sm:grid-cols-3 pt-2">
            <Field label="Delivery location"><Input value={details.delivery_location} onChange={(e) => setDetails({ ...details, delivery_location: e.target.value })} /></Field>
            <Field label="Needed by"><Input type="date" value={details.target_date} onChange={(e) => setDetails({ ...details, target_date: e.target.value })} /></Field>
            <Field label="Product family">
              <Select value={family} onChange={(e) => setFamily(e.target.value)}>
                <option value="">General</option>
                {families.data?.families.filter((f) => f.active).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </Select>
            </Field>
          </div>
          <Field label="Special requirements"><Input value={details.special_requirements} onChange={(e) => setDetails({ ...details, special_requirements: e.target.value })} /></Field>
          <div className="flex flex-wrap items-center gap-4">
            <Field label="Language for replies" className="w-48">
              <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                <option value="">English</option>
                {Object.entries(LANGUAGES).filter(([k]) => k !== "en").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </Field>
            <label className="flex items-center gap-2 text-[13px] pt-5"><input type="checkbox" checked={tender} onChange={(e) => setTender(e.target.checked)} /> A tender</label>
          </div>
        </div>
      </Card>
      {save.error && (
        <div role="alert" className="text-red-500 text-[13px]">
          {problems.length ? <ul className="list-disc pl-5">{problems.map((p, i) => <li key={i}>{p}</li>)}</ul> : errorMessage(save.error, "Could not save the enquiry")}
        </div>
      )}
      <div className="flex gap-2">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save the enquiry"}</Button>
        <Link to="/enquiries"><Button variant="secondary">Cancel</Button></Link>
      </div>
      <p className="text-[12px] text-ink-300">Forge gives it an owner and starts the quote timer. As you have already spoken to the customer, no acknowledgement is sent.</p>
    </div>
  )
}
