import { Link, NavLink, Outlet } from "react-router-dom"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useEnquiryCounts, useMailbox } from "./hooks"

const TABS = [
  { to: "/enquiries", label: "Queue", permission: "enquiries.read", end: true },
  { to: "/enquiries/other-mail", label: "Other mail", permission: "enquiries.read", count: "other_mail" as const },
  { to: "/enquiries/test-mailbox", label: "Test mailbox", permission: "enquiries.work" },
  { to: "/enquiries/setup", label: "Setup", permission: "enquiries.read" },
]

export function EnquiriesLayout() {
  const perms = usePermissions()
  const counts = useEnquiryCounts()
  const mailbox = useMailbox()
  const mb = mailbox.data?.mailbox
  const connected = mb && mb.status !== "disconnected"
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-2xl font-bold text-ink-900">Enquiries</h1>
        {mailbox.data && (
          <span className={cn("rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold",
            connected && mb.status === "active" ? "bg-green-100 text-green-500"
              : connected ? "bg-red-100 text-red-500" : "bg-gold-100 text-ink-700")}>
            {connected
              ? mb.status === "active" ? `Reading ${mb.address ?? "the sales mailbox"}`
                : mb.status === "paused" ? "Mailbox paused" : "Mailbox needs attention"
              : "No mailbox yet: Test mailbox only"}
          </span>
        )}
        {counts.data && counts.data.unassigned > 0 && (
          <span className="rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold bg-amber-100 text-amber-500">
            {counts.data.unassigned} without an owner
          </span>
        )}
      </div>
      {mailbox.data && mb?.status === "broken" && (
        <div role="alert" className="rounded-md bg-red-100 text-red-500 text-[13px] p-3">
          <b>The sales mailbox stopped working:</b> <span className="text-ink-700">{mb.last_error ?? "it could not be read"}.</span>{" "}
          New enquiries are not coming in. <Link to="/enquiries/setup?tab=mailbox" className="underline font-semibold">Check the mailbox</Link>
        </div>
      )}
      {counts.data && counts.data.failed > 0 && (
        <div role="alert" className="rounded-md bg-amber-100 text-ink-700 text-[13px] p-3">
          <b>{counts.data.failed} {counts.data.failed === 1 ? "email" : "emails"} could not be read.</b>{" "}
          <Link to="/enquiries/other-mail?show=failed" className="underline font-semibold">Look at them</Link>
        </div>
      )}
      <nav className="flex gap-1 border-b border-line overflow-x-auto -mx-1 px-1">
        {TABS.filter((t) => hasPermission(perms, t.permission)).map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}
            className={({ isActive }) => cn(
              "px-3 py-2 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900 whitespace-nowrap",
              isActive && "text-ink-900 border-teal-500")}>
            {t.label}
            {t.count && (counts.data?.[t.count] ?? 0) > 0 && (
              <span className="ml-1.5 rounded-full bg-surface-2 text-ink-700 text-[10.5px] font-bold px-1.5">{counts.data?.[t.count]}</span>
            )}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
