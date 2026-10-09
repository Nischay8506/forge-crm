import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDate, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import type { InboxSettings } from "@/features/agents/types"
import { useInboxSettings } from "./hooks"
import { workingHours } from "./labels"

const ZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "America/New_York", "UTC"]

type Draft = Omit<InboxSettings, "is_default" | "updated_by" | "updated_at" | "day_names" | "roles" | "escalation_role"
  | "default_sla_hours" | "reminder_pct" | "bottleneck_overdue" | "bottleneck_wait_ratio"> & {
  default_sla_hours: string; reminder_pct: string; bottleneck_overdue: string; bottleneck_wait_ratio: string
}

function draftOf(s: InboxSettings): Draft {
  return {
    time_zone: s.time_zone, work_days: s.work_days, day_start: s.day_start, day_end: s.day_end,
    holidays: s.holidays, default_sla_hours: s.default_sla_hours, reminder_pct: String(s.reminder_pct),
    escalation_role_id: s.escalation_role_id, bottleneck_overdue: String(s.bottleneck_overdue),
    bottleneck_wait_ratio: s.bottleneck_wait_ratio,
  }
}

/** H4 and H9: the working calendar deadlines are counted in, reminders, escalation and
 * the bottleneck alert. Missing a deadline never approves anything (safeguard 4). */
function SettingsForm({ s, canEdit }: { s: InboxSettings; canEdit: boolean }) {
  const qc = useQueryClient()
  const [d, setD] = useState<Draft>(() => draftOf(s))
  const [holiday, setHoliday] = useState({ date: "", name: "" })
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }))
  const save = useMutation({
    mutationFn: async () => (await api.put<InboxSettings>("/decisions/settings", {
      ...d, default_sla_hours: d.default_sla_hours, reminder_pct: Number(d.reminder_pct),
      bottleneck_overdue: Number(d.bottleneck_overdue), bottleneck_wait_ratio: d.bottleneck_wait_ratio,
      escalation_role_id: d.escalation_role_id || null })).data,
    onSuccess: (saved) => {
      qc.setQueryData(["decisions", "settings"], saved)
      setD(draftOf(saved))
    },
  })
  const problems = errorProblems(save.error)
  const ro = !canEdit

  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <Card>
        <CardHeader><CardTitle>Working calendar</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-[12.5px] text-ink-500">Deadlines count only working hours: a 4-hour card raised at 17:30 on the last working day is due late the next working morning.</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="Time zone">
              <Select disabled={ro} value={d.time_zone} onChange={(e) => set({ time_zone: e.target.value })}>
                {[...new Set([d.time_zone, ...ZONES])].map((z) => <option key={z} value={z}>{z}</option>)}
              </Select>
            </Field>
            <Field label="Working day starts"><Input disabled={ro} type="time" value={d.day_start} onChange={(e) => set({ day_start: e.target.value })} /></Field>
            <Field label="Working day ends"><Input disabled={ro} type="time" value={d.day_end} onChange={(e) => set({ day_end: e.target.value })} /></Field>
          </div>
          <Field label="Working days" group>
            <div className="flex flex-wrap gap-2">
              {s.day_names.map((name, i) => {
                const on = d.work_days.includes(i)
                return (
                  <button key={name} type="button" disabled={ro} aria-pressed={on}
                    onClick={() => set({ work_days: on ? d.work_days.filter((x) => x !== i) : [...d.work_days, i].sort() })}
                    className={cn("rounded-md border px-2.5 py-1.5 text-[12.5px]",
                      on ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500")}>
                    {name.slice(0, 3)}
                  </button>
                )
              })}
            </div>
          </Field>
          <Field label="Holidays" group hint="Days off on top of the weekly ones.">
            {d.holidays.length === 0 && <p className="text-[12.5px] text-ink-300">None yet.</p>}
            <ul className="space-y-1">
              {[...d.holidays].sort((a, b) => a.date.localeCompare(b.date)).map((h) => (
                <li key={h.date} className="flex items-center gap-3 text-[13px]">
                  <span className="w-28 text-ink-700">{formatDate(h.date)}</span>
                  <span className="flex-1 text-ink-900">{h.name}</span>
                  {!ro && <button type="button" className="text-[12px] text-ink-500 hover:text-red-500"
                    onClick={() => set({ holidays: d.holidays.filter((x) => x.date !== h.date) })}>Remove</button>}
                </li>
              ))}
            </ul>
            {!ro && (
              <div className="flex flex-wrap gap-2 pt-1">
                <Input type="date" className="max-w-[170px]" value={holiday.date} onChange={(e) => setHoliday((h) => ({ ...h, date: e.target.value }))} />
                <Input placeholder="Name, e.g. Diwali" className="max-w-[220px]" value={holiday.name} onChange={(e) => setHoliday((h) => ({ ...h, name: e.target.value }))} />
                <Button type="button" variant="secondary" size="sm" className="h-[38px]" disabled={!holiday.date || !holiday.name.trim()}
                  onClick={() => { set({ holidays: [...d.holidays.filter((x) => x.date !== holiday.date), { date: holiday.date, name: holiday.name.trim() }] }); setHoliday({ date: "", name: "" }) }}>
                  Add holiday
                </Button>
              </div>
            )}
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Deadlines, reminders and escalation</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="Default deadline per step" hint={`In working hours (${workingHours(d.default_sla_hours)}). A policy rule can set its own.`}>
              <Input disabled={ro} inputMode="decimal" value={d.default_sla_hours} onChange={(e) => set({ default_sla_hours: e.target.value })} />
            </Field>
            <Field label="Reminder at" hint="Percent of the time gone, e.g. 75.">
              <Input disabled={ro} inputMode="numeric" value={d.reminder_pct} onChange={(e) => set({ reminder_pct: e.target.value })} />
            </Field>
            <Field label="Past the deadline, tell" hint="When a card has no next step to tell.">
              <Select disabled={ro} value={d.escalation_role_id ?? ""} onChange={(e) => set({ escalation_role_id: e.target.value || null })}>
                <option value="">Full-access admins</option>
                {s.roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </Select>
            </Field>
          </div>
          <p className="text-[12.5px] bg-surface-2 rounded-md p-2 text-ink-700">
            A missed deadline never approves or skips anything. The card stays waiting for its own approvers;
            the next role in the chain (or the role above) is told and sees it in their inbox.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Bottleneck alert</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Field label="Alert when one role has more than this many cards past their deadline">
            <Input disabled={ro} inputMode="numeric" value={d.bottleneck_overdue} onChange={(e) => set({ bottleneck_overdue: e.target.value })} />
          </Field>
          <Field label="Or when a card type's median wait grows by this factor week on week">
            <Input disabled={ro} inputMode="decimal" value={d.bottleneck_wait_ratio} onChange={(e) => set({ bottleneck_wait_ratio: e.target.value })} />
          </Field>
        </CardContent>
      </Card>

      {problems.length > 0 && (
        <ul className="bg-red-100 text-red-500 text-[12.5px] p-2 rounded-md space-y-0.5">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      <ErrorText error={problems.length ? null : save.error} fallback="Could not save the settings" />
      <div className="flex flex-wrap items-center gap-3">
        {canEdit && <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save settings"}</Button>}
        {save.isSuccess && <span className="text-[12.5px] text-green-500">Saved. New deadlines use these settings; running ones keep theirs.</span>}
        <span className="text-[11.5px] text-ink-300">
          {s.is_default ? "Using Forge's starting settings." : <>Last changed by <Actor actor={s.updated_by} /> on {formatDateTime(s.updated_at)}.</>}
          {!canEdit && " Only people with the decisions.manage permission can change these."}
        </span>
      </div>
    </form>
  )
}

export function DeadlinesPage() {
  const canEdit = hasPermission(usePermissions(), "decisions.manage")
  const { data, isLoading, error } = useInboxSettings()
  return (
    <div className="space-y-3">
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the settings" />
      {data && <SettingsForm s={data} canEdit={canEdit} />}
    </div>
  )
}
