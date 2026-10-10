import { Link } from "react-router-dom"
import { Tag } from "@/components/ui/Tag"
import { cn, formatDateTime } from "@/lib/utils"
import { CHANNEL_LABELS, PURPOSE_LABELS, STATUS_TONE, partyLabel, snippet, waitLabel } from "./labels"
import type { MessageView } from "./types"

/** One message in a list: status, who, subject, first line, and why it waits. */
export function MessageRow({ m, showChannel = false, className }: { m: MessageView; showChannel?: boolean; className?: string }) {
  const wait = waitLabel(m)
  return (
    <Link to={`/messages/${m.id}`} className={cn("block px-3 py-2.5 hover:bg-surface-2 text-[13px]", className)}>
      <div className="flex flex-wrap items-center gap-1.5">
        {showChannel && <Tag>{CHANNEL_LABELS[m.channel] ?? m.channel}</Tag>}
        <Tag variant={STATUS_TONE[m.status] ?? "neutral"}>{m.status_label ?? m.status}</Tag>
        {m.purpose && m.direction === "out" && <Tag>{PURPOSE_LABELS[m.purpose] ?? m.purpose}</Tag>}
        {m.ai_generated && <Tag variant="teal">AI</Tag>}
        <span className="text-ink-700 font-medium">{partyLabel(m)}</span>
        <span className="flex-1" />
        <span className="text-ink-300 text-[12px]">{formatDateTime(m.sent_at ?? m.created_at)}</span>
      </div>
      <div className="mt-0.5 min-w-0">
        {m.subject && <span className="font-semibold text-ink-900">{m.subject}</span>}
        <span className="text-ink-500">{m.subject ? " · " : ""}{snippet(m.body)}</span>
      </div>
      {(wait || (m.reason && ["failed", "bounced", "blocked"].includes(m.status))) && (
        <p className={cn("text-[12px] mt-0.5", ["failed", "bounced", "blocked"].includes(m.status) ? "text-red-500" : "text-amber-500")}>
          {wait ?? m.reason}
        </p>
      )}
    </Link>
  )
}
