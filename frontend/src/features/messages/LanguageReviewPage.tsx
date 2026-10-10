import { useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/config/api"
import { Button } from "@/components/ui/Button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card"
import { Input, Select, inputClass } from "@/components/ui/Field"
import { Empty, ErrorText, Loading } from "@/components/ui/States"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn, formatDateTime } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { Actor } from "@/features/steward/Actor"
import { useLanguages } from "./hooks"
import { REVIEW_WHY, partyLabel, scoreLabel } from "./labels"
import type { Review } from "./types"

function ReviewCard({ r, canReview }: { r: Review; canReview: boolean }) {
  const qc = useQueryClient()
  const m = r.message
  const [score, setScore] = useState<number | null>(null)
  const [comment, setComment] = useState("")
  const [editing, setEditing] = useState(false)
  const [subject, setSubject] = useState(m?.subject ?? "")
  const [body, setBody] = useState(m?.body ?? "")
  const canChange = r.blocking && m?.status === "review"
  const decide = useMutation({
    mutationFn: async (verdict: "approve" | "change" | "reject") => (await api.post(`/languages/reviews/${r.id}`, {
      verdict, score, comment: comment.trim() || null,
      ...(verdict === "change" ? { subject, body } : {}) })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviews"] })
      qc.invalidateQueries({ queryKey: ["languages"] })
      qc.invalidateQueries({ queryKey: ["messages"] })
    },
  })
  return (
    <Card>
      <CardContent className="space-y-3 pt-4">
        <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
          <Tag variant="teal">{m?.language ? m.language.toUpperCase() : r.language.toUpperCase()}</Tag>
          <Tag variant={r.why === "checks" ? "amber" : "neutral"}>{REVIEW_WHY[r.why] ?? r.why}</Tag>
          {r.blocking ? <Tag variant="gold">Waits for you</Tag> : <Tag>Already sent</Tag>}
          {m && <span className="text-ink-700">{partyLabel(m)}</span>}
          <span className="flex-1" />
          <span className="text-ink-300">{formatDateTime(r.created_at)}</span>
        </div>
        {r.problems.length > 0 && (
          <ul className="text-[12.5px] text-amber-500 space-y-0.5">{r.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        )}
        {m && !editing && (
          <div className="rounded-md bg-surface-2 p-3 text-[13.5px] space-y-1.5">
            <p className="font-semibold text-ink-900">{m.subject}</p>
            <p className="whitespace-pre-wrap text-ink-900 leading-relaxed">{m.body}</p>
            {m.disclosure && <p className="text-ink-500 italic text-[12.5px]">{m.disclosure}</p>}
          </div>
        )}
        {editing && (
          <div className="space-y-2">
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Corrected subject" />
            <textarea rows={8} value={body} onChange={(e) => setBody(e.target.value)} aria-label="Corrected text"
              className={cn(inputClass, "leading-relaxed")} />
          </div>
        )}
        {m && <Link to={`/messages/${m.id}`} className="text-[12px] text-teal-600 hover:underline">Open the message</Link>}
        {canReview ? (
          <div className="space-y-2 border-t border-line pt-3">
            <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
              <span className="text-ink-700 font-semibold">How natural is the writing?</span>
              {[1, 2, 3, 4, 5].map((n) => (
                <button key={n} type="button" aria-pressed={score === n} onClick={() => setScore(score === n ? null : n)}
                  className={cn("w-8 h-8 rounded-md border text-[13px]", score === n ? "border-teal-500 bg-teal-100 text-ink-900 font-bold" : "border-line text-ink-500")}>
                  {n}
                </button>
              ))}
              <span className="text-ink-300">1 = wrong or unnatural, 5 = as a native writer would</span>
            </div>
            <Input placeholder="What should be better? (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
            <ErrorText error={decide.error} fallback="Could not save the review" />
            <div className="flex flex-wrap gap-2">
              {editing ? (
                <>
                  <Button size="sm" disabled={!body.trim() || decide.isPending} onClick={() => decide.mutate("change")}>
                    {canChange ? "Save the correction and send" : "Save the correction"}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>Back</Button>
                </>
              ) : (
                <>
                  <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
                    {canChange ? "Fine, send it" : "Fine"}
                  </Button>
                  {canChange && <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>Correct it</Button>}
                  <Button size="sm" variant="danger" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
                    {canChange ? "Do not send" : "Not acceptable"}
                  </Button>
                </>
              )}
            </div>
          </div>
        ) : (
          <p className="text-[12px] text-ink-300 border-t border-line pt-2">Only people with the languages.review permission (native speakers) can review.</p>
        )}
      </CardContent>
    </Card>
  )
}

function DoneRow({ r }: { r: Review }) {
  return (
    <li className="py-2 text-[12.5px] flex flex-wrap items-center gap-2">
      <Tag variant="teal">{r.language.toUpperCase()}</Tag>
      <Tag variant={r.status === "rejected" ? "red" : r.status === "changed" ? "amber" : "green"}>{r.status}</Tag>
      {r.score !== null && <span className="text-ink-700">{r.score} / 5</span>}
      {r.message && <Link to={`/messages/${r.message.id}`} className="text-teal-600 hover:underline">{r.message.subject || "(no subject)"}</Link>}
      {r.comment && <span className="text-ink-500">“{r.comment}”</span>}
      <span className="flex-1" />
      <span className="text-ink-300"><Actor actor={r.reviewed_by} /> · {formatDateTime(r.reviewed_at)}</span>
    </li>
  )
}

/** Native speakers check AI-written messages (L11 A): every one whose checks found a
 *  problem, and a sample of the rest. */
export function LanguageReviewPage() {
  const canReview = hasPermission(usePermissions(), "languages.review")
  const langs = useLanguages()
  const [language, setLanguage] = useState("")
  const [show, setShow] = useState<"waiting" | "done">("waiting")
  const { data, isLoading, error } = useQuery({
    queryKey: ["reviews", show, language],
    queryFn: async () => (await api.get<Review[]>("/languages/reviews", { params: {
      status: show === "waiting" ? "waiting" : "approved,changed,rejected", language: language || undefined, limit: 100 } })).data,
  })
  const report = langs.data?.report ?? []

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>Reviews per language</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {report.map((r) => (
              <div key={r.language} className="rounded-md bg-surface-2 p-3 text-[12.5px] space-y-1">
                <div className="flex items-center justify-between">
                  <b className="text-ink-900 text-[13.5px]">{r.name}</b>
                  <Tag variant={r.met ? "green" : "gold"}>{r.reviewed} of {r.target}</Tag>
                </div>
                <div className="h-1.5 rounded-full bg-line overflow-hidden">
                  <div className="h-full bg-teal-500" style={{ width: `${Math.min(100, (r.reviewed / r.target) * 100)}%` }} />
                </div>
                <div className="text-ink-500">
                  Average {scoreLabel(r.average_score)} · {r.changed} corrected · {r.rejected} not acceptable · {r.waiting} waiting
                </div>
              </div>
            ))}
          </div>
          <p className="text-[11.5px] text-ink-300 mt-2">F12.5 asks for at least {report[0]?.target ?? 10} reviewed messages per language Forge writes in.</p>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        {(["waiting", "done"] as const).map((k) => (
          <button key={k} type="button" onClick={() => setShow(k)}
            className={cn("rounded-full border px-3 py-1 text-[12.5px]", show === k ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500")}>
            {k === "waiting" ? "Waiting" : "Done"}
          </button>
        ))}
        <Select className="w-44 h-8 py-1 text-[12.5px]" value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Language">
          <option value="">Every language</option>
          {(langs.data?.recognised ?? []).filter((l) => l.writes).map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
        </Select>
      </div>
      {isLoading && <Loading />}
      <ErrorText error={error} fallback="Could not load the reviews" />
      {data && data.length === 0 && <Card><Empty>{show === "waiting" ? "Nothing to review." : "No reviews yet."}</Empty></Card>}
      {data && show === "waiting" && data.map((r) => <ReviewCard key={r.id} r={r} canReview={canReview} />)}
      {data && show === "done" && data.length > 0 && (
        <Card><CardContent className="pt-2"><ul className="divide-y divide-line">{data.map((r) => <DoneRow key={r.id} r={r} />)}</ul></CardContent></Card>
      )}
    </div>
  )
}
