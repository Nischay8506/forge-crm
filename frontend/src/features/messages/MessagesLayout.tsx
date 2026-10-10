import { Link, NavLink, Outlet } from "react-router-dom"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useChannels } from "./hooks"

const TABS = [
  { to: "/messages", label: "Messages", permission: "accounts.read", end: true },
  { to: "/messages/new", label: "New message", permission: "messages.send" },
  { to: "/messages/test-inbox", label: "Test inbox", permission: "accounts.read" },
  { to: "/messages/reviews", label: "Language review", permission: "accounts.read" },
  { to: "/messages/languages", label: "Languages", permission: "accounts.read" },
  { to: "/messages/settings", label: "Settings", permission: "accounts.read" },
]

export function MessagesLayout() {
  const perms = usePermissions()
  const { data } = useChannels()
  const account = data?.email_account
  const live = data?.settings.email_mode === "live"
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-2xl font-bold text-ink-900">Messages</h1>
        {data && (
          <span className={cn("rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold",
            live ? "bg-green-100 text-green-500" : "bg-gold-100 text-ink-700")}>
            {live ? `Email: live from ${account?.from_address ?? "the company mailbox"}` : "Email: Test inbox only"}
          </span>
        )}
      </div>
      {data && !live && (
        <p className="text-[12.5px] text-ink-500 -mt-2">
          Nothing leaves Forge: every email goes to the <Link to="/messages/test-inbox" className="text-teal-600 hover:underline">Test inbox</Link>,
          where you can reply, bounce or unsubscribe as the customer would. An administrator switches to live sending under Settings.
        </p>
      )}
      {live && account?.status === "paused" && (
        <div role="alert" className="rounded-md bg-amber-100 text-ink-700 text-[13px] p-3">
          <b>Sending is paused.</b> Emails wait until an administrator resumes the email account under{" "}
          <Link to="/messages/settings" className="underline font-semibold">Settings</Link>.
        </div>
      )}
      {live && account?.last_check_ok === false && account.status !== "paused" && (
        <div role="alert" className="rounded-md bg-red-100 text-red-500 text-[13px] p-3">
          <b>The email account stopped working:</b> <span className="text-ink-700">{account.last_check_message ?? "the mail server refused it"}.</span>{" "}
          Emails are held and retried. <Link to="/messages/settings" className="underline font-semibold">Check the account</Link>
        </div>
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
