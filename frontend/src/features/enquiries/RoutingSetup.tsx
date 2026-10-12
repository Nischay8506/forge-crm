import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { useEnquirySettings, useFamilies, usePeople } from "./hooks"
import type { EnquirySettings, Person } from "./types"

/** Who gets a new enquiry (N12 A): the account's owner, then someone who looks after the
 *  product family, then the customer's region, then the next in the team with the fewest
 *  open enquiries. An owner who is away (Forge's Away) is replaced by their stand-in. */
export function RoutingSetup({ canManage }: { canManage: boolean }) {
  const settings = useEnquirySettings()
  const people = usePeople()
  if (settings.isLoading || people.isLoading || !settings.data || !people.data) return <Loading />
  return (
    <div className="space-y-4">
      {settings.data.suggestions.map((s) => (
        <p key={s.rule} role="status" className="rounded-md bg-amber-100 text-ink-700 text-[13px] p-3">{s.text}</p>
      ))}
      <StepsCard s={settings.data} people={people.data} canManage={canManage} />
      <PeopleCard people={people.data} canManage={canManage} teamMax={settings.data.routing.max_open} />
    </div>
  )
}

function StepsCard({ s, people, canManage }: { s: EnquirySettings; people: Person[]; canManage: boolean }) {
  const qc = useQueryClient()
  const [steps, setSteps] = useState(s.routing.steps)
  const [maxOpen, setMaxOpen] = useState(String(s.routing.max_open))
  const [fallback, setFallback] = useState(s.routing.fallback_user_id ?? "")
  const save = useMutation({
    mutationFn: async () => (await api.put<EnquirySettings>("/enquiries/setup/settings/routing", {
      steps, max_open: Number(maxOpen), fallback_user_id: fallback || null,
    })).data,
    onSuccess: (data) => qc.setQueryData(["enquiries", "settings"], data),
  })
  const all = Object.keys(s.step_labels)
  const move = (i: number, d: number) => {
    const next = [...steps]
    const [x] = next.splice(i, 1)
    next.splice(i + d, 0, x)
    setSteps(next)
  }
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle>Who gets a new enquiry</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-3 text-[13px]">
        <p className="text-ink-500">Forge tries these steps in order and stops at the first that finds someone who takes enquiries and is under their limit. The dealer step comes later.</p>
        <ol className="space-y-1.5">
          {steps.map((k, i) => (
            <li key={k} className="flex items-center gap-2 rounded-md border border-line px-3 py-2">
              <span className="font-mono text-ink-300 w-5">{i + 1}</span>
              <span className="flex-1">{s.step_labels[k] ?? k}</span>
              {canManage && (
                <>
                  <button type="button" disabled={i === 0} onClick={() => move(i, -1)} className="text-teal-600 disabled:text-ink-300 px-1" aria-label="Move up">↑</button>
                  <button type="button" disabled={i === steps.length - 1} onClick={() => move(i, 1)} className="text-teal-600 disabled:text-ink-300 px-1" aria-label="Move down">↓</button>
                  <button type="button" disabled={steps.length === 1} onClick={() => setSteps(steps.filter((x) => x !== k))} className="text-[12px] text-red-500 disabled:text-ink-300">Leave out</button>
                </>
              )}
            </li>
          ))}
        </ol>
        {canManage && all.filter((k) => !steps.includes(k)).map((k) => (
          <button key={k} type="button" onClick={() => setSteps([...steps, k])} className="mr-2 text-[12.5px] text-teal-600 hover:underline">+ {s.step_labels[k]}</button>
        ))}
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Most open enquiries per person" hint="A person's own limit below overrides this">
            <Input value={maxOpen} onChange={(e) => setMaxOpen(e.target.value)} inputMode="numeric" disabled={!canManage} />
          </Field>
          <Field label="If no step finds anyone" hint="Without a person here, the escalation role (or admins) is told">
            <Select value={fallback} onChange={(e) => setFallback(e.target.value)} disabled={!canManage}>
              <option value="">Leave it without an owner</option>
              {people.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.full_name}</option>)}
            </Select>
          </Field>
        </div>
        {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
        {save.isSuccess && <p role="status" className="text-green-500 text-[12.5px]">Saved.</p>}
        {canManage && <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>}
      </div>
    </Card>
  )
}

function PeopleCard({ people, canManage, teamMax }: { people: Person[]; canManage: boolean; teamMax: number }) {
  const [editing, setEditing] = useState<string | null>(null)
  const families = useFamilies()
  const famName = (id: string) => families.data?.families.find((f) => f.id === id)?.name ?? "?"
  return (
    <Card className="overflow-x-auto">
      <CardHeader className="pb-2"><CardTitle>People</CardTitle></CardHeader>
      <p className="px-4 pb-2 text-[12.5px] text-ink-500">
        Who takes enquiries, what they look after, and who they report to. Late enquiries go to the manager, then to the manager's manager (the head of department).
      </p>
      <table className="w-full text-[13px] min-w-[760px]">
        <thead>
          <tr className="text-left text-ink-500 border-y border-line">
            <th className="p-3 font-semibold">Person</th>
            <th className="p-3 font-semibold">Takes enquiries</th>
            <th className="p-3 font-semibold">Product families</th>
            <th className="p-3 font-semibold">Regions</th>
            <th className="p-3 font-semibold">Open / limit</th>
            <th className="p-3 font-semibold">Reports to</th>
            <th className="p-3" />
          </tr>
        </thead>
        <tbody>
          {people.filter((p) => p.is_active).map((p) => editing === p.id ? (
            <tr key={p.id}><td colSpan={7} className="p-3"><PersonForm p={p} people={people} onDone={() => setEditing(null)} /></td></tr>
          ) : (
            <tr key={p.id} className="border-b border-line last:border-0 align-top">
              <td className="p-3">
                <div className="font-semibold text-ink-900">{p.full_name}</div>
                {p.away && <Tag variant="amber">Away{p.stand_in_name ? ` · ${p.stand_in_name} stands in` : ""}</Tag>}
              </td>
              <td className="p-3">{p.takes_enquiries ? "Yes" : <span className="text-ink-300">No</span>}</td>
              <td className="p-3">{p.product_family_ids.map(famName).join(", ") || <span className="text-ink-300">–</span>}</td>
              <td className="p-3">{p.regions.join(", ") || <span className="text-ink-300">–</span>}</td>
              <td className="p-3 font-mono">{p.open} / {p.max_open_enquiries ?? `${teamMax} (team)`}</td>
              <td className="p-3">{p.reports_to_name ?? <span className="text-ink-300">–</span>}</td>
              <td className="p-3 text-right">{canManage && <button type="button" onClick={() => setEditing(p.id)} className="text-[12px] text-teal-600 hover:underline">Change</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

function PersonForm({ p, people, onDone }: { p: Person; people: Person[]; onDone: () => void }) {
  const qc = useQueryClient()
  const families = useFamilies()
  const [takes, setTakes] = useState(p.takes_enquiries)
  const [fams, setFams] = useState<string[]>(p.product_family_ids)
  const [regions, setRegions] = useState(p.regions.join(", "))
  const [max, setMax] = useState(p.max_open_enquiries ? String(p.max_open_enquiries) : "")
  const [boss, setBoss] = useState(p.reports_to_id ?? "")
  const save = useMutation({
    mutationFn: async () => api.put(`/enquiries/setup/people/${p.id}`, {
      takes_enquiries: takes, product_family_ids: fams,
      regions: regions.split(",").map((r) => r.trim()).filter(Boolean),
      max_open_enquiries: max ? Number(max) : null, reports_to_id: boss || null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["enquiries", "people"] }); onDone() },
  })
  return (
    <div className="rounded-md border border-teal-500 p-3 space-y-2">
      <div className="font-semibold text-ink-900">{p.full_name}</div>
      <label className="flex items-center gap-2"><input type="checkbox" checked={takes} onChange={(e) => setTakes(e.target.checked)} /> Takes enquiries</label>
      <div className="grid gap-2 sm:grid-cols-3">
        <Field label="Regions" hint="States or cities, separated by commas"><Input value={regions} onChange={(e) => setRegions(e.target.value)} placeholder="Telangana, Hyderabad" /></Field>
        <Field label="Own limit of open enquiries" hint="Empty: the team limit"><Input value={max} onChange={(e) => setMax(e.target.value)} inputMode="numeric" /></Field>
        <Field label="Reports to">
          <Select value={boss} onChange={(e) => setBoss(e.target.value)}>
            <option value="">No one</option>
            {people.filter((x) => x.is_active && x.id !== p.id).map((x) => <option key={x.id} value={x.id}>{x.full_name}</option>)}
          </Select>
        </Field>
      </div>
      <Field label="Product families" group>
        <div className="flex flex-wrap gap-2">
          {families.data?.families.map((f) => (
            <label key={f.id} className="flex items-center gap-1.5 text-[13px]">
              <input type="checkbox" checked={fams.includes(f.id)} onChange={(e) => setFams(e.target.checked ? [...fams, f.id] : fams.filter((x) => x !== f.id))} />
              {f.name}
            </label>
          ))}
          {families.data?.families.length === 0 && <span className="text-ink-300 text-[12.5px]">No product families yet: add them under Product families.</span>}
        </div>
      </Field>
      {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
        <Button size="sm" variant="secondary" onClick={onDone}>Cancel</Button>
      </div>
    </div>
  )
}
