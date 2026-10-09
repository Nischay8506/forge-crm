import { useEffect, useState } from "react"
import { NavLink, Outlet } from "react-router-dom"
import { errorMessage } from "@/config/api"
import { Tag } from "@/components/ui/Tag"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useDecisionCount, useMyNumbers } from "./hooks"
import { pct, secondsLabel } from "./labels"
import { type PushState, disablePush, enablePush, pushState } from "./push"

const TABS = [
  { to: "/decisions", label: "Inbox", permission: "decisions.decide", end: true },
  { to: "/decisions/away", label: "Away and hand-overs", permission: "decisions.decide" },
  { to: "/decisions/analytics", label: "Approval numbers", permission: "decisions.manage" },
  { to: "/decisions/deadlines", label: "Deadlines", permission: "decisions.decide" },
]

const PUSH_TEXT: Record<PushState, string> = {
  unsupported: "This browser can't show alerts",
  not_set_up: "Alerts are not set up on this server",
  blocked: "Alerts are blocked in this browser's settings",
  on: "Alerts on this device: on",
  off: "Turn on alerts on this device",
}

/** H5: new cards, reminders and escalations as notifications on this device. */
function PushToggle() {
  const [state, setState] = useState<PushState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { pushState().then(setState).catch(() => setState("unsupported")) }, [])
  if (state === null) return null
  const can = state === "on" || state === "off"
  return (
    <span className="flex items-center gap-2">
      <button type="button" disabled={!can || busy}
        title={state === "not_set_up" ? "An administrator sets the push keys (see the handover document)" : undefined}
        onClick={async () => {
          setBusy(true)
          setError(null)
          try {
            setState(state === "on" ? await disablePush() : await enablePush())
          } catch (e) {
            setError(errorMessage(e, "Could not change alerts on this device"))
          } finally {
            setBusy(false)
          }
        }}
        className={cn("text-[12px] rounded-full border px-2.5 py-1",
          state === "on" ? "border-teal-500 text-teal-600 bg-teal-100" : "border-line text-ink-500",
          can ? "hover:text-ink-900" : "opacity-70 cursor-default")}>
        {busy ? "…" : PUSH_TEXT[state]}
      </button>
      {error && <span className="text-[11.5px] text-red-500">{error}</span>}
    </span>
  )
}

export function DecisionsLayout() {
  const perms = usePermissions()
  const count = useDecisionCount(true)
  const mine = useMyNumbers()
  const c = count.data
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-2xl font-bold text-ink-900">Decisions</h1>
        {!!c?.waiting && <Tag variant="gold">{c.waiting} waiting</Tag>}
        {!!c?.near_deadline && <Tag variant="amber">{c.near_deadline} near deadline</Tag>}
        {!!c?.past_deadline && <Tag variant="red">{c.past_deadline} past deadline</Tag>}
        <span className="flex-1" />
        <PushToggle />
      </div>
      {mine.data && mine.data.decided > 0 && (
        <p className="text-[12px] text-ink-300 -mt-2">
          Your median decision time in the last {mine.data.days} days: <b className="text-ink-700">{secondsLabel(mine.data.median_decision_seconds)}</b>
          {" · "}your override rate on agent recommendations: <b className="text-ink-700">{pct(mine.data.override_rate)}</b>
          {mine.data.with_recommendation > 0 && ` (${mine.data.overrides} of ${mine.data.with_recommendation})`}
        </p>
      )}
      <nav className="flex gap-1 border-b border-line overflow-x-auto -mx-1 px-1">
        {TABS.filter((t) => hasPermission(perms, t.permission)).map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}
            className={({ isActive }) => cn(
              "px-3 py-2 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900 whitespace-nowrap",
              isActive && "text-ink-900 border-teal-500")}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
