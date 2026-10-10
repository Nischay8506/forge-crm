import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorProblems } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDate, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { useChannels } from "./hooks"
import { DAY_SHORT } from "./labels"
import type { ChannelSettings, ChannelsView, EmailAccount } from "./types"

function LiveSwitch({ c, canEdit }: { c: ChannelsView; canEdit: boolean }) {
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const live = c.settings.email_mode === "live"
  const a = c.email_account
  const ready = !!a && a.status === "active" && a.last_check_ok === true
  const mode = useMutation({
    mutationFn: async (m: "simulator" | "live") => (await api.post("/channels/mode", { mode: m })).data,
    onSuccess: () => { setConfirm(false); qc.invalidateQueries({ queryKey: ["channels"] }) },
  })
  return (
    <Card>
      <CardHeader><CardTitle>Where email goes</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-[13px]">
        <div className="flex flex-wrap items-center gap-2">
          <Tag variant={live ? "green" : "gold"}>{live ? "Live" : "Test inbox"}</Tag>
          <span className="text-ink-700">
            {live ? `Emails go to customers from ${a?.from_address ?? "the company mailbox"}.` : "Emails go only to the Test inbox. Nothing reaches a customer."}
          </span>
        </div>
        {!c.links_public && (
          <p className="rounded-md bg-amber-100 p-2 text-[12.5px] text-ink-700">
            The unsubscribe link in promotional emails points at this computer (<code>PUBLIC_BASE_URL</code> in backend/.env),
            so customers cannot open it. Before sending real promotional email, Forge needs a public https address.
          </p>
        )}
        {canEdit && !live && !confirm && (
          <div className="space-y-1">
            <Button size="sm" disabled={!ready} onClick={() => setConfirm(true)}>Switch to live sending</Button>
            {!ready && <p className="text-[12px] text-ink-300">First set up the email account below and run its check.</p>}
          </div>
        )}
        {confirm && (
          <div className="rounded-md bg-gold-100 p-3 space-y-2 text-[12.5px]">
            <p><b>Real customers will get emails from {a?.from_address}.</b> Held and queued messages go at their time. Messages already in the Test inbox are not sent again.</p>
            <div className="flex gap-2">
              <Button size="sm" variant="gold" disabled={mode.isPending} onClick={() => mode.mutate("live")}>Yes, send real email</Button>
              <Button size="sm" variant="secondary" onClick={() => setConfirm(false)}>Back</Button>
            </div>
          </div>
        )}
        {canEdit && live && (
          <Button size="sm" variant="secondary" disabled={mode.isPending} onClick={() => mode.mutate("simulator")}>Back to the Test inbox</Button>
        )}
        <ErrorText error={mode.error} fallback="Could not switch" />
      </CardContent>
    </Card>
  )
}

const EMPTY_ACCOUNT = { host: "", port: "587", security: "starttls", username: "", password: "", from_address: "", from_name: "", reply_to: "" }

function accountDraft(a: EmailAccount | null) {
  if (!a) return EMPTY_ACCOUNT
  return { host: a.host, port: String(a.port), security: a.security, username: a.username, password: "",
    from_address: a.from_address, from_name: a.from_name ?? "", reply_to: a.reply_to ?? "" }
}

function AccountCard({ c, canEdit }: { c: ChannelsView; canEdit: boolean }) {
  const qc = useQueryClient()
  const a = c.email_account
  const [d, setD] = useState(() => accountDraft(a))
  const [preset, setPreset] = useState("")
  const set = (patch: Partial<typeof d>) => setD((x) => ({ ...x, ...patch }))
  const refresh = () => qc.invalidateQueries({ queryKey: ["channels"] })
  const save = useMutation({
    mutationFn: async () => (await api.put<EmailAccount>("/channels/email-account", {
      ...d, port: Number(d.port), password: d.password || null, from_name: d.from_name || null, reply_to: d.reply_to || null })).data,
    onSuccess: (saved) => { setD(accountDraft(saved)); refresh() },
  })
  const check = useMutation({
    mutationFn: async () => (await api.post<{ ok: boolean; message: string }>("/channels/email-account/check")).data,
    onSuccess: refresh,
  })
  const pause = useMutation({
    mutationFn: async (paused: boolean) => (await api.post("/channels/email-account/pause", { paused })).data,
    onSuccess: refresh,
  })
  const help = preset ? c.presets[preset]?.help : null
  const ro = !canEdit || !c.credential_keys_configured
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>Email account</CardTitle>
        {a && (
          <span className="flex items-center gap-2 text-[12px]">
            {a.status === "paused" && <Tag variant="amber">Paused</Tag>}
            <Tag variant={a.last_check_ok ? "green" : a.last_check_ok === false ? "red" : "neutral"}>
              {a.last_check_ok ? "Works" : a.last_check_ok === false ? "Not working" : "Not checked"}
            </Tag>
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {!c.credential_keys_configured && (
          <p className="rounded-md bg-amber-100 p-2 text-[12.5px] text-ink-700">
            <b>Forge cannot store the email password yet.</b> An administrator sets <code>CREDENTIAL_KEYS</code> in backend/.env
            (run <code>uv run python -m app.cli new-credential-key</code>), then restarts the backend and worker.
          </p>
        )}
        <p className="text-[12.5px] text-ink-500">The company mailbox Forge sends from (SMTP). The password is stored encrypted and never shown again.</p>
        {canEdit && (
          <Field label="Mail provider" hint={help ?? undefined}>
            <Select value={preset} disabled={ro} onChange={(e) => {
              const p = c.presets[e.target.value]
              setPreset(e.target.value)
              if (p) set({ host: p.host || d.host, port: String(p.port), security: p.security })
            }}>
              <option value="">Choose to fill in the server…</option>
              {Object.entries(c.presets).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
            </Select>
          </Field>
        )}
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_100px_140px] gap-3">
            <Field label="SMTP server"><Input disabled={ro} value={d.host} placeholder="smtp.example.com" onChange={(e) => set({ host: e.target.value })} /></Field>
            <Field label="Port"><Input disabled={ro} inputMode="numeric" value={d.port} onChange={(e) => set({ port: e.target.value })} /></Field>
            <Field label="Security">
              <Select disabled={ro} value={d.security} onChange={(e) => set({ security: e.target.value })}>
                <option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="none">None (test servers only)</option>
              </Select>
            </Field>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Login"><Input disabled={ro} value={d.username} autoComplete="off" onChange={(e) => set({ username: e.target.value })} /></Field>
            <Field label="Password or app password" hint={a?.password_hint ? `Stored, ends in ${a.password_hint}. Leave empty to keep it.` : undefined}>
              <Input disabled={ro} type="password" autoComplete="new-password" value={d.password} onChange={(e) => set({ password: e.target.value })} />
            </Field>
            <Field label="Send from (address)"><Input disabled={ro} value={d.from_address} placeholder="sales@yourcompany.in" onChange={(e) => set({ from_address: e.target.value })} /></Field>
            <Field label="Sender name"><Input disabled={ro} value={d.from_name} placeholder="Your Company Sales" onChange={(e) => set({ from_name: e.target.value })} /></Field>
            <Field label="Replies go to (optional)"><Input disabled={ro} value={d.reply_to} onChange={(e) => set({ reply_to: e.target.value })} /></Field>
          </div>
          <ErrorText error={save.error} fallback="Could not save the account" />
          {canEdit && (
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" size="sm" disabled={ro || save.isPending}>Save</Button>
              {a && <Button type="button" size="sm" variant="secondary" disabled={check.isPending} onClick={() => check.mutate()}>
                {check.isPending ? "Checking…" : "Check it works"}</Button>}
              {a && <Button type="button" size="sm" variant="secondary" disabled={pause.isPending} onClick={() => pause.mutate(a.status !== "paused")}>
                {a.status === "paused" ? "Resume sending" : "Pause sending"}</Button>}
              {save.isSuccess && <span className="text-[12.5px] text-green-500">Saved. Run the check before sending live.</span>}
            </div>
          )}
        </form>
        {check.data && (
          <p className={cn("text-[12.5px] rounded-md p-2", check.data.ok ? "bg-green-100 text-green-500" : "bg-red-100 text-red-500")}>{check.data.message}</p>
        )}
        <ErrorText error={check.error ?? pause.error} fallback="Could not do that" />
        {a?.last_check_at && (
          <p className="text-[11.5px] text-ink-300">
            Last checked {formatDateTime(a.last_check_at)}{a.last_check_message ? `: ${a.last_check_message}` : ""}. Checked again by itself every 15 minutes while live.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

type Draft = Omit<ChannelSettings, "email_mode" | "is_default" | "updated_by" | "updated_at" | "promo_per_day" | "promo_per_week"
  | "reply_window_hours" | "review_first" | "review_every"> & {
  promo_per_day: string; promo_per_week: string; reply_window_hours: string; review_first: string; review_every: string
}

function draftOf(s: ChannelSettings): Draft {
  return {
    promo_per_day: String(s.promo_per_day), promo_per_week: String(s.promo_per_week), quiet_start: s.quiet_start,
    quiet_end: s.quiet_end, promo_rest_days: s.promo_rest_days, promo_skip_holidays: s.promo_skip_holidays,
    reply_window_hours: String(s.reply_window_hours), review_first: String(s.review_first),
    review_every: String(s.review_every), staff_email: s.staff_email,
  }
}

const num = (v: string) => (/^\d+$/.test(v.trim()) ? Number(v) : v)

function RulesForm({ c, canEdit }: { c: ChannelsView; canEdit: boolean }) {
  const qc = useQueryClient()
  const s = c.settings
  const [d, setD] = useState<Draft>(() => draftOf(s))
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }))
  const save = useMutation({
    mutationFn: async () => (await api.put<ChannelSettings>("/channels/settings", {
      ...d, promo_per_day: num(d.promo_per_day), promo_per_week: num(d.promo_per_week),
      reply_window_hours: num(d.reply_window_hours), review_first: num(d.review_first), review_every: num(d.review_every) })).data,
    onSuccess: (saved) => { setD(draftOf(saved)); qc.invalidateQueries({ queryKey: ["channels"] }) },
  })
  const problems = errorProblems(save.error)
  const ro = !canEdit
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
      <Card>
        <CardHeader><CardTitle>Promotional limits</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-[12.5px] text-ink-500">Per customer, across every channel. Messages about their business with us and replies are not limited.</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="At most per day" hint="A calendar day in the company's time zone.">
              <Input disabled={ro} inputMode="numeric" value={d.promo_per_day} onChange={(e) => set({ promo_per_day: e.target.value })} />
            </Field>
            <Field label="At most in any 7 days">
              <Input disabled={ro} inputMode="numeric" value={d.promo_per_week} onChange={(e) => set({ promo_per_week: e.target.value })} />
            </Field>
          </div>
          <Field label="No promotional messages on" group>
            <div className="flex flex-wrap gap-2">
              {DAY_SHORT.map((name, i) => {
                const on = d.promo_rest_days.includes(i)
                return (
                  <button key={name} type="button" disabled={ro} aria-pressed={on}
                    onClick={() => set({ promo_rest_days: on ? d.promo_rest_days.filter((x) => x !== i) : [...d.promo_rest_days, i].sort() })}
                    className={cn("rounded-md border px-2.5 py-1.5 text-[12.5px]", on ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500")}>
                    {name}
                  </button>
                )
              })}
            </div>
          </Field>
          <label className="flex items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-1" disabled={ro} checked={d.promo_skip_holidays} onChange={(e) => set({ promo_skip_holidays: e.target.checked })} />
            <span>
              Not on company holidays either
              <span className="block text-[12px] text-ink-300">
                {c.holidays.length ? `${c.holidays.length} holidays: ${c.holidays.slice(0, 4).map(formatDate).join(", ")}${c.holidays.length > 4 ? "…" : ""}. ` : "No holidays set yet. "}
                They come from the <Link to="/decisions/deadlines" className="text-teal-600 hover:underline">working calendar</Link>.
              </span>
            </span>
          </label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Quiet hours</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="From"><Input disabled={ro} type="time" value={d.quiet_start} onChange={(e) => set({ quiet_start: e.target.value })} /></Field>
            <Field label="Until"><Input disabled={ro} type="time" value={d.quiet_end} onChange={(e) => set({ quiet_end: e.target.value })} /></Field>
            <Field label="Reply window (hours)" hint="A reply to a customer who wrote within this time goes at once, even in quiet hours.">
              <Input disabled={ro} inputMode="numeric" value={d.reply_window_hours} onChange={(e) => set({ reply_window_hours: e.target.value })} />
            </Field>
          </div>
          <p className="text-[12px] text-ink-300">In {c.time_zone}, the company's time zone. A message written in quiet hours is held and goes when they end.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Language reviews and staff emails</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Review every AI-written message until" hint="This many have been reviewed in a language.">
              <Input disabled={ro} inputMode="numeric" value={d.review_first} onChange={(e) => set({ review_first: e.target.value })} />
            </Field>
            <Field label="Then review one in" hint="Messages whose checks find a problem are always reviewed.">
              <Input disabled={ro} inputMode="numeric" value={d.review_every} onChange={(e) => set({ review_every: e.target.value })} />
            </Field>
          </div>
          <label className="flex items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-1" disabled={ro} checked={d.staff_email} onChange={(e) => set({ staff_email: e.target.checked })} />
            <span>Email approval cards to the people who decide them
              <span className="block text-[12px] text-ink-300">The email links to the card; nothing can be approved from the email itself.</span></span>
          </label>
        </CardContent>
      </Card>

      {problems.length > 0 && (
        <ul className="bg-red-100 text-red-500 text-[12.5px] p-2 rounded-md space-y-0.5">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
      )}
      <ErrorText error={problems.length ? null : save.error} fallback="Could not save the settings" />
      <div className="flex flex-wrap items-center gap-3">
        {canEdit && <Button type="submit" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save settings"}</Button>}
        {save.isSuccess && <span className="text-[12.5px] text-green-500">Saved. Held messages keep their time and are checked against these settings when it comes.</span>}
        <span className="text-[11.5px] text-ink-300">
          {s.is_default ? "Using Forge's starting settings." : <>Last changed by <Actor actor={s.updated_by} /> on {formatDateTime(s.updated_at)}.</>}
          {!canEdit && " Only people with the channels.manage permission can change these."}
        </span>
      </div>
    </form>
  )
}

export function ChannelSettingsPage() {
  const canEdit = hasPermission(usePermissions(), "channels.manage")
  const { data, isLoading, error } = useChannels()
  if (isLoading) return <Loading />
  if (error || !data) return <ErrorText error={error} fallback="Could not load the settings" />
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
      <div className="space-y-4">
        <LiveSwitch c={data} canEdit={canEdit} />
        <AccountCard key={data.email_account?.updated_at ?? "new"} c={data} canEdit={canEdit} />
        <Card>
          <CardHeader><CardTitle>WhatsApp and SMS</CardTitle></CardHeader>
          <CardContent className="text-[12.5px] text-ink-500">On the later list (decisions L3 and L4). Consent and the limits here already cover them.</CardContent>
        </Card>
      </div>
      <RulesForm key={data.settings.updated_at ?? "default"} c={data} canEdit={canEdit} />
    </div>
  )
}
