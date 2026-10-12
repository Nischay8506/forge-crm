import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input, Select } from "@/components/ui/Field"
import { Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { useMailbox } from "./hooks"
import type { MailboxView } from "./types"

const STATUS: Record<MailboxView["status"], { tone: "green" | "amber" | "red" | "neutral"; text: string }> = {
  active: { tone: "green", text: "Reading" },
  paused: { tone: "amber", text: "Paused" },
  broken: { tone: "red", text: "Needs attention" },
  disconnected: { tone: "neutral", text: "Disconnected" },
}

/** The sales mailbox Forge reads (N3 A): Gmail or Microsoft 365 by sign-in, or any
 *  other provider by IMAP. Forge only reads; it never deletes or moves mail. */
export function MailboxSetup({ canManage, notice }: { canManage: boolean; notice: { connected?: string | null; error?: string | null } }) {
  const qc = useQueryClient()
  const { data, isLoading } = useMailbox()
  const [imapOpen, setImapOpen] = useState(false)
  const done = () => qc.invalidateQueries({ queryKey: ["enquiries", "mailbox"] })
  const oauth = useMutation({
    mutationFn: async (provider: "gmail" | "microsoft") => (await api.post<{ url: string }>(`/enquiries/setup/mailbox/oauth/${provider}`)).data,
    onSuccess: (r) => { window.location.href = r.url },
  })
  const checkNow = useMutation({
    mutationFn: async () => (await api.post<{ kept?: number; error?: string }>("/enquiries/setup/mailbox/check")).data,
    onSuccess: done,
  })
  const pause = useMutation({ mutationFn: async (paused: boolean) => api.post("/enquiries/setup/mailbox/pause", { paused }), onSuccess: done })
  const disconnect = useMutation({ mutationFn: async () => api.delete("/enquiries/setup/mailbox"), onSuccess: done })
  if (isLoading || !data) return <Loading />
  const mb = data.mailbox
  const connected = mb && mb.status !== "disconnected"
  const err = oauth.error ?? checkNow.error ?? pause.error ?? disconnect.error

  return (
    <div className="space-y-4 max-w-[760px]">
      {notice.connected && <p role="status" className="rounded-md bg-green-100 text-green-500 text-[13px] p-3">Connected. Forge reads new mail from now on.</p>}
      {notice.error && <p role="alert" className="rounded-md bg-red-100 text-red-500 text-[13px] p-3">The mailbox was not connected: {notice.error}</p>}
      <Card>
        <CardHeader className="pb-2"><CardTitle>Sales mailbox</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-3 text-[13px]">
          {!connected ? (
            <p className="text-ink-500">No mailbox is connected. Until one is, try Forge with the Test mailbox.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink-900">{mb.address ?? "(address not known yet)"}</span>
                <Tag>{mb.provider_name}</Tag>
                <Tag variant={STATUS[mb.status].tone}>{STATUS[mb.status].text}</Tag>
                {mb.push && <Tag variant="teal">Instant (push)</Tag>}
              </div>
              <dl className="grid grid-cols-[150px_1fr] gap-x-2 gap-y-1 text-[12.5px]">
                <dt className="text-ink-500">Last checked</dt><dd>{formatDateTime(mb.last_poll_at)}</dd>
                <dt className="text-ink-500">Last worked</dt><dd>{formatDateTime(mb.last_ok_at)}</dd>
                {mb.provider === "imap" && <><dt className="text-ink-500">Server</dt><dd>{mb.imap_host}:{mb.imap_port} ({mb.imap_security}) · {mb.folder}</dd></>}
                {mb.secret_hint && <><dt className="text-ink-500">Saved sign-in</dt><dd className="font-mono">{mb.secret_hint}</dd></>}
                {mb.last_error && <><dt className="text-ink-500">Last problem</dt><dd className="text-red-500">{mb.last_error}{mb.failures > 1 ? ` (${mb.failures} times)` : ""}</dd></>}
              </dl>
              <p className="text-[12px] text-ink-300">Forge reads the mailbox from the moment it was connected; older mail is not read. It only reads: nothing is deleted or moved.</p>
              {checkNow.data && (
                <p className="text-[12.5px] text-ink-700">{checkNow.data.error ? `The check failed: ${checkNow.data.error}` : `Checked: ${checkNow.data.kept ?? 0} new ${checkNow.data.kept === 1 ? "email" : "emails"}.`}</p>
              )}
              {canManage && (
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" onClick={() => checkNow.mutate()} disabled={checkNow.isPending}>{checkNow.isPending ? "Checking…" : "Check now"}</Button>
                  {mb.status === "paused"
                    ? <Button size="sm" variant="secondary" onClick={() => pause.mutate(false)}>Resume</Button>
                    : <Button size="sm" variant="secondary" onClick={() => pause.mutate(true)}>Pause</Button>}
                  <Button size="sm" variant="danger" onClick={() => { if (confirm("Disconnect the mailbox? Forge stops reading it and forgets the sign-in.")) disconnect.mutate() }}>Disconnect</Button>
                </div>
              )}
            </>
          )}
          {err && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(err, "That did not work")}</p>}
        </div>
      </Card>
      {canManage && (
        <Card>
          <CardHeader className="pb-2"><CardTitle>{connected ? "Connect a different mailbox" : "Connect the mailbox"}</CardTitle></CardHeader>
          <div className="px-4 pb-4 space-y-3 text-[13px]">
            <div className="grid gap-3 sm:grid-cols-2">
              {(["gmail", "microsoft"] as const).map((p) => (
                <div key={p} className="rounded-md border border-line p-3 space-y-2">
                  <div className="font-semibold text-ink-900">{p === "gmail" ? "Gmail / Google Workspace" : "Microsoft 365 / Outlook"}</div>
                  {data[p].ready ? (
                    <Button size="sm" onClick={() => oauth.mutate(p)} disabled={oauth.isPending}>Sign in with {p === "gmail" ? "Google" : "Microsoft"}</Button>
                  ) : (
                    <p className="text-[12px] text-ink-500">{data[p].missing}</p>
                  )}
                  {p === "gmail" && data[p].ready && <p className="text-[11.5px] text-ink-300">Until Google reviews Forge's app, Google asks to sign in again every 7 days.</p>}
                </div>
              ))}
            </div>
            {imapOpen ? <ImapForm onDone={() => { setImapOpen(false); done() }} onCancel={() => setImapOpen(false)} current={mb} />
              : <Button size="sm" variant="secondary" onClick={() => setImapOpen(true)}>Another provider (IMAP)</Button>}
          </div>
        </Card>
      )}
    </div>
  )
}

function ImapForm({ onDone, onCancel, current }: { onDone: () => void; onCancel: () => void; current: MailboxView | null }) {
  const same = current?.provider === "imap"
  const [v, setV] = useState({
    address: same ? current.address ?? "" : "", imap_host: same ? current.imap_host ?? "" : "",
    imap_port: same && current.imap_port ? String(current.imap_port) : "", imap_security: (same ? current.imap_security : null) ?? "ssl",
    username: same ? current.username ?? "" : "", password: "", folder: (same ? current.folder : null) ?? "INBOX",
  })
  const save = useMutation({
    mutationFn: async () => api.put("/enquiries/setup/mailbox/imap", {
      ...v, imap_port: v.imap_port ? Number(v.imap_port) : null, username: v.username || null, password: v.password || null,
    }),
    onSuccess: onDone,
  })
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV({ ...v, [k]: e.target.value })
  return (
    <div className="rounded-md border border-teal-500 p-3 space-y-2">
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="Mailbox address"><Input value={v.address} onChange={set("address")} placeholder="sales@yourcompany.in" /></Field>
        <Field label="IMAP server"><Input value={v.imap_host} onChange={set("imap_host")} placeholder="imap.yourprovider.com" /></Field>
        <Field label="Port" hint="Empty: 993 for SSL, 143 for STARTTLS"><Input value={v.imap_port} onChange={set("imap_port")} inputMode="numeric" /></Field>
        <Field label="Security">
          <Select value={v.imap_security} onChange={set("imap_security")}>
            <option value="ssl">SSL/TLS</option>
            <option value="starttls">STARTTLS</option>
          </Select>
        </Field>
        <Field label="User name" hint="Empty: the mailbox address"><Input value={v.username} onChange={set("username")} autoComplete="off" /></Field>
        <Field label="Password" hint={same ? "Empty keeps the saved password" : "Use an app password if the provider offers one"}>
          <Input type="password" value={v.password} onChange={set("password")} autoComplete="new-password" />
        </Field>
        <Field label="Folder"><Input value={v.folder} onChange={set("folder")} /></Field>
      </div>
      <p className="text-[11.5px] text-ink-300">Forge signs in once to test it before saving. The password is kept encrypted and is never shown again.</p>
      {save.error && <p role="alert" className="text-red-500 text-[12.5px]">{errorMessage(save.error, "Could not connect")}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending || !v.address || !v.imap_host}>{save.isPending ? "Testing…" : "Test and connect"}</Button>
        <Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}
