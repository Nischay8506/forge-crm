import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { useEnquirySettings } from "./hooks"
import { UNIT_LABELS } from "./labels"
import type { EnquirySettings, StageSetting } from "./types"

/** The timers (N13 A, and the engineering review from P2 A) and when people are told,
 *  plus how Forge handles new mail. */
export function TimersSetup({ canManage }: { canManage: boolean }) {
  const { data, isLoading } = useEnquirySettings()
  if (isLoading || !data) return <Loading />
  return (
    <div className="space-y-4 max-w-[760px]">
      <TimersCard s={data} canManage={canManage} />
      <IntakeCard s={data} canManage={canManage} />
    </div>
  )
}

function TimersCard({ s, canManage }: { s: EnquirySettings; canManage: boolean }) {
  const qc = useQueryClient()
  const [stages, setStages] = useState<StageSetting[]>(s.timers.stages)
  const [pct, setPct] = useState({ reminder_pct: String(s.timers.reminder_pct), manager_pct: String(s.timers.manager_pct), hod_pct: String(s.timers.hod_pct) })
  const save = useMutation({
    mutationFn: async () => (await api.put<EnquirySettings>("/enquiries/setup/settings/timers", {
      stages: stages.map((x) => ({ ...x, target: Number(x.target) })),
      reminder_pct: Number(pct.reminder_pct), manager_pct: Number(pct.manager_pct), hod_pct: Number(pct.hod_pct),
    })).data,
    onSuccess: (data) => qc.setQueryData(["enquiries", "settings"], data),
  })
  const setStage = (i: number, patch: Partial<StageSetting>) => setStages(stages.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle>Timers</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-3 text-[13px]">
        <p className="text-ink-500">Working time follows the business calendar under Decisions → Deadlines. The acknowledgement counts as the first response.</p>
        {stages.map((x, i) => (
          <div key={x.key} className="grid gap-2 sm:grid-cols-[1fr_110px_220px] items-end">
            <Field label="Stage"><Input value={x.label} onChange={(e) => setStage(i, { label: e.target.value })} disabled={!canManage} /></Field>
            <Field label="Within"><Input value={String(x.target)} onChange={(e) => setStage(i, { target: e.target.value as unknown as number })} inputMode="decimal" disabled={!canManage} /></Field>
            <Field label="Unit">
              <Select value={x.unit} onChange={(e) => setStage(i, { unit: e.target.value as StageSetting["unit"] })} disabled={!canManage}>
                {Object.entries(UNIT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </Field>
          </div>
        ))}
        <div className="grid gap-2 sm:grid-cols-3">
          <Field label="Remind the owner at (%)"><Input value={pct.reminder_pct} onChange={(e) => setPct({ ...pct, reminder_pct: e.target.value })} inputMode="numeric" disabled={!canManage} /></Field>
          <Field label="Tell the manager at (%)"><Input value={pct.manager_pct} onChange={(e) => setPct({ ...pct, manager_pct: e.target.value })} inputMode="numeric" disabled={!canManage} /></Field>
          <Field label="Tell the head of department at (%)"><Input value={pct.hod_pct} onChange={(e) => setPct({ ...pct, hod_pct: e.target.value })} inputMode="numeric" disabled={!canManage} /></Field>
        </div>
        <p className="text-[12px] text-ink-300">People are told in Forge and by email. For the engineering review, the engineer who took it (before that, everyone in the engineering role) is reminded and their manager is told. Changes apply to running timers too.</p>
        {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
        {save.isSuccess && <p role="status" className="text-green-500 text-[12.5px]">Saved.</p>}
        {canManage && <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save timers</Button>}
      </div>
    </Card>
  )
}

function IntakeCard({ s, canManage }: { s: EnquirySettings; canManage: boolean }) {
  const qc = useQueryClient()
  const roles = useQuery({
    queryKey: ["roles"],
    queryFn: async () => (await api.get<{ id: string; name: string }[]>("/roles")).data,
    enabled: canManage,
    retry: false,
  })
  const [v, setV] = useState({
    auto_acknowledge: s.intake.auto_acknowledge, nudge_days: s.intake.nudge_days.join(", "),
    park_after_days: String(s.intake.park_after_days), complaints_role_id: s.intake.complaints_role_id ?? "",
    unsure_below: String(Math.round(s.intake.unsure_below * 100)),
  })
  const save = useMutation({
    mutationFn: async () => (await api.put<EnquirySettings>("/enquiries/setup/settings/intake", {
      auto_acknowledge: v.auto_acknowledge,
      nudge_days: v.nudge_days.split(",").map((x) => x.trim()).filter(Boolean).map(Number),
      park_after_days: Number(v.park_after_days), complaints_role_id: v.complaints_role_id || null,
      unsure_below: Number(v.unsure_below) / 100,
    })).data,
    onSuccess: (data) => qc.setQueryData(["enquiries", "settings"], data),
  })
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle>New mail</CardTitle></CardHeader>
      <div className="px-4 pb-4 space-y-3 text-[13px]">
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-1" checked={v.auto_acknowledge} onChange={(e) => setV({ ...v, auto_acknowledge: e.target.checked })} disabled={!canManage} />
          <span>Acknowledge new enquiries automatically when the AI read them confidently. Strategic accounts and tenders always get a draft for a person; how much the Enquiry Agent may do per account tier is set under Agents.</span>
        </label>
        <div className="grid gap-2 sm:grid-cols-3">
          <Field label="Remind the customer on day" hint="Up to three days, e.g. 2, 5"><Input value={v.nudge_days} onChange={(e) => setV({ ...v, nudge_days: e.target.value })} disabled={!canManage} /></Field>
          <Field label="Park after (days)" hint="After the last reminder"><Input value={v.park_after_days} onChange={(e) => setV({ ...v, park_after_days: e.target.value })} inputMode="numeric" disabled={!canManage} /></Field>
          <Field label="Unsure below (%)" hint="Under this, a person checks it"><Input value={v.unsure_below} onChange={(e) => setV({ ...v, unsure_below: e.target.value })} inputMode="numeric" disabled={!canManage} /></Field>
        </div>
        <Field label="Tell this role about complaints" hint="Empty: administrators">
          <Select value={v.complaints_role_id} onChange={(e) => setV({ ...v, complaints_role_id: e.target.value })} disabled={!canManage}>
            <option value="">Administrators</option>
            {roles.data?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </Select>
        </Field>
        {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not save")}</p>}
        {save.isSuccess && <p role="status" className="text-green-500 text-[12.5px]">Saved.</p>}
        {canManage && <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>}
      </div>
    </Card>
  )
}
