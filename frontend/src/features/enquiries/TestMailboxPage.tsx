import { useRef, useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, errorMessage } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardHeader, CardTitle } from "@/components/ui/Card"
import { Field, Input } from "@/components/ui/Field"
import { Empty, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { formatDateTime } from "@/lib/utils"
import { readAsBase64 } from "./files"
import { MAIL_LABELS } from "./labels"
import type { TestMailResult, TestMailRow } from "./types"

const SAMPLES: Record<string, { label: string; from_name: string; from_address: string; subject: string; body: string }> = {
  en: {
    label: "English RFQ", from_name: "Ravi Kumar", from_address: "ravi@sve.example.in", subject: "RFQ for MS plates and beams",
    body: "Dear Sir,\n\nPlease quote your best rates for the following:\n\n1. MS Plate 10mm x 1500 x 6300, IS 2062 E250 - 25 MT\n2. ISMB 200 - 12 MT\n3. ISMC 150 - 8 MT\n\nDelivery at: Patancheru, Hyderabad\nRequired by 30/11/2026\n\nRegards,\nRavi Kumar\nPurchase Manager\nSri Venkateswara Engineering Pvt Ltd\nHyderabad, Telangana\nMob: 98480 12345",
  },
  hi: {
    label: "Hindi request", from_name: "Suresh Agarwal", from_address: "suresh@agarwal-fab.example.in", subject: "कोटेशन चाहिए",
    body: "नमस्ते,\n\nहमें 20 MT TMT बार Fe 500D (12mm) चाहिए। कृपया रेट भेजें।\nडिलीवरी: इंदौर, मध्य प्रदेश\n\nधन्यवाद,\nसुरेश अग्रवाल\nअग्रवाल फैब्रिकेटर्स\n98260 55443",
  },
  te: {
    label: "Telugu request", from_name: "Lakshmi Reddy", from_address: "lakshmi@reddy-eng.example.in", subject: "ఫ్లాంజ్‌ల కోసం",
    body: "నమస్కారం,\n\nమాకు 500 nos ఫ్లాంజ్‌లు కావాలి (150 NB, IS 6392). ధర తెలియజేయండి.\nవిజయవాడకు డెలివరీ.\n\nలక్ష్మి రెడ్డి\nరెడ్డి ఇంజనీరింగ్ వర్క్స్",
  },
  other: {
    label: "Payment mail", from_name: "Accounts, Deccan Pumps", from_address: "accounts@deccanpumps.example.in", subject: "Payment done for invoice 4471",
    body: "Dear team,\n\nWe have paid invoice 4471 by NEFT today, UTR SBIN0012345. Kindly confirm receipt.\n\nRegards,\nAccounts team",
  },
}

/** Write an email as a customer would. It goes through everything a real email does:
 *  reading, duplicates, owner, timers and the acknowledgement. Forge's answers land in
 *  the Test inbox, never in a real mailbox. */
export function TestMailboxPage() {
  const qc = useQueryClient()
  const [v, setV] = useState({ from_name: "", from_address: "", subject: "", body: "" })
  const [replyTo, setReplyTo] = useState<{ id: string; subject: string | null } | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const fileInput = useRef<HTMLInputElement>(null)
  const [last, setLast] = useState<TestMailResult | null>(null)
  const recent = useQuery({
    queryKey: ["enquiries", "test-recent"],
    queryFn: async () => (await api.get<TestMailRow[]>("/enquiries/test-mailbox/recent")).data,
    refetchInterval: 10_000,
  })
  const send = useMutation({
    mutationFn: async () => {
      const total = files.reduce((n, f) => n + f.size, 0)
      if (total > 20 * 1024 * 1024) throw new Error("Attach up to 20 MB in a test email")
      const attachments = await Promise.all(files.map(readAsBase64))
      return (await api.post<TestMailResult>("/enquiries/test-mailbox", { ...v, reply_to_message_id: replyTo?.id ?? null, attachments })).data
    },
    onSuccess: (r) => {
      setLast(r)
      setFiles([])
      setReplyTo(null)
      if (fileInput.current) fileInput.current.value = ""
      qc.invalidateQueries({ queryKey: ["enquiries"] })
    },
  })
  const fill = (k: keyof typeof SAMPLES) => {
    const s = SAMPLES[k]
    setV({ from_name: s.from_name, from_address: s.from_address, subject: s.subject, body: s.body })
    setReplyTo(null)
  }
  const answer = (row: TestMailRow, r: TestMailRow["replies"][number]) => {
    setV({ from_name: row.from_name ?? "", from_address: row.from_address ?? "", subject: r.subject?.startsWith("Re:") ? r.subject : `Re: ${r.subject ?? ""}`, body: "" })
    setReplyTo({ id: r.id, subject: r.subject })
    window.scrollTo({ top: 0, behavior: "smooth" })
  }
  const err = send.error instanceof Error && !("isAxiosError" in send.error) ? send.error.message : send.error ? errorMessage(send.error, "Could not send the test email") : null

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
      <Card>
        <CardHeader className="pb-2"><CardTitle>Write a test email</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-3">
          <p className="text-[12.5px] text-ink-500">
            Write as a customer. Forge reads it like a real email; anything it sends back goes to the{" "}
            <Link to="/messages/test-inbox" className="text-teal-600 hover:underline">Test inbox</Link>.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(SAMPLES).map(([k, s]) => (
              <button key={k} type="button" onClick={() => fill(k as keyof typeof SAMPLES)}
                className="rounded-full border border-line px-2.5 py-0.5 text-[12px] text-ink-500 hover:text-ink-900 hover:border-teal-500">{s.label}</button>
            ))}
          </div>
          {replyTo && (
            <div className="rounded-md bg-teal-100 text-ink-700 text-[12.5px] p-2 flex items-center justify-between gap-2">
              <span>Answering Forge's message “{replyTo.subject}”, in the same thread.</span>
              <button type="button" className="text-teal-600 hover:underline" onClick={() => setReplyTo(null)}>Not a reply</button>
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="From (name)"><Input value={v.from_name} onChange={(e) => setV({ ...v, from_name: e.target.value })} /></Field>
            <Field label="From (email)"><Input value={v.from_address} onChange={(e) => setV({ ...v, from_address: e.target.value })} type="email" /></Field>
          </div>
          <Field label="Subject"><Input value={v.subject} onChange={(e) => setV({ ...v, subject: e.target.value })} /></Field>
          <Field label="Message">
            <textarea value={v.body} onChange={(e) => setV({ ...v, body: e.target.value })} rows={10}
              className="w-full rounded-md border border-input bg-surface-1 px-3 py-2 text-[13.5px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-teal-500" />
          </Field>
          <Field label="Files" hint="Excel (.xlsx), PDF, Word (.docx), CSV, text or pictures; up to 10 files">
            <input ref={fileInput} type="file" multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []).slice(0, 10))} className="text-[13px]" />
          </Field>
          {err && <p role="alert" className="text-red-500 text-[13px]">{err}</p>}
          <Button onClick={() => send.mutate()} disabled={send.isPending || !v.from_address.trim() || (!v.body.trim() && files.length === 0)}>
            {send.isPending ? "Reading it…" : "Send to Forge"}
          </Button>
          {last && (
            <div role="status" className="rounded-md bg-surface-2 p-3 text-[13px]">
              {last.enquiry_id ? (
                <>
                  {last.outcome === "reply" ? "Added to " : "Became "}
                  <Link to={`/enquiries/${last.enquiry_id}`} className="font-semibold text-teal-600 hover:underline">{last.ref}</Link>
                  {last.label_source === "rules" && " (read by rules: no AI key yet)"}
                </>
              ) : (
                <>Not an enquiry: kept in <Link to="/enquiries/other-mail" className="text-teal-600 hover:underline">Other mail</Link> as {MAIL_LABELS[last.label ?? ""] ?? last.label ?? last.outcome}.</>
              )}
            </div>
          )}
        </div>
      </Card>
      <Card>
        <CardHeader className="pb-2"><CardTitle>Test emails and Forge's answers</CardTitle></CardHeader>
        <div className="px-4 pb-4 space-y-2">
          {recent.isLoading && <Loading />}
          {recent.data?.length === 0 && <Empty>No test emails yet.</Empty>}
          {recent.data?.map((row) => (
            <div key={row.id} className="rounded-md border border-line p-2.5 text-[13px] space-y-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                {row.ref ? <Link to={`/enquiries/${row.enquiry_id}`} className="font-mono text-[11.5px] font-semibold text-teal-600 hover:underline">{row.ref}</Link>
                  : <Tag>{MAIL_LABELS[row.label ?? ""] ?? row.outcome ?? row.stage}</Tag>}
                <span className="font-medium text-ink-700">{row.subject || "(no subject)"}</span>
                <span className="flex-1" />
                <span className="text-ink-300 text-[11.5px]">{formatDateTime(row.received_at)}</span>
              </div>
              {row.replies.map((r) => (
                <div key={r.id} className="ml-3 rounded bg-surface-0 border border-line p-2">
                  <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
                    <Tag variant="teal">Forge</Tag><span className="text-ink-700">{r.subject}</span>
                    <Tag>{r.status}</Tag>
                    <span className="flex-1" />
                    {["sent", "delivered", "read"].includes(r.status) && (
                      <button type="button" className="text-teal-600 hover:underline" onClick={() => answer(row, r)}>Reply as the customer</button>
                    )}
                  </div>
                  <pre className="whitespace-pre-wrap font-body text-[12.5px] text-ink-700 mt-1 max-h-40 overflow-auto">{r.body}</pre>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
