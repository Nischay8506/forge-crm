import { Link, useLocation } from "react-router-dom"
import { useAuthStore, usePermissions } from "@/stores/authStore"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { useDecisionCount } from "@/features/decisions/hooks"
import { useOverview } from "@/features/integrations/hooks"
import { useEnquiryCounts } from "@/features/enquiries/hooks"

/** Nav items appear only when the user holds the permission (null = everyone).
 *  Add each module's pages here as they are built. */
const NAV: { name: string; path: string; permission: string | null }[] = [
  { name: "Home", path: "/", permission: null },
  { name: "Enquiries", path: "/enquiries", permission: "enquiries.read" },
  { name: "Accounts", path: "/accounts", permission: "accounts.read" },
  { name: "Contacts", path: "/contacts", permission: "accounts.read" },
  { name: "Data Steward", path: "/steward", permission: "steward.read" },
  { name: "Decisions", path: "/decisions", permission: "decisions.decide" },
  { name: "Messages", path: "/messages", permission: "accounts.read" },
  { name: "Agents", path: "/agents", permission: "agents.read" },
  { name: "Integrations", path: "/integrations", permission: "integrations.read" },
  { name: "Users", path: "/admin/users", permission: "users.read" },
]

export function AppBar() {
  const { me, logout } = useAuthStore()
  const permissions = usePermissions()
  const location = useLocation()
  const count = useDecisionCount(hasPermission(permissions, "decisions.decide"))
  const waiting = count.data?.waiting ?? 0
  const overview = useOverview(hasPermission(permissions, "integrations.read"))
  const erpBroken = (overview.data?.connections ?? []).some((c) => c.health === "problem")
  const enquiries = useEnquiryCounts(hasPermission(permissions, "enquiries.read"))
  const unowned = enquiries.data?.unassigned ?? 0
  const initials = me?.full_name
    ? me.full_name.split(" ").map((n) => n[0]).join("").substring(0, 2).toUpperCase()
    : "U"

  return (
    <header className="flex items-center gap-2 sm:gap-3 bg-ink-700 px-3 sm:px-4 h-[52px] w-full shrink-0 sticky top-0 z-20">
      <Link to="/" className="font-disp font-bold text-white text-[14px] tracking-[0.02em] shrink-0">
        FORGE<b className="text-gold-500 font-bold mx-[1px]">·</b>CRM
      </Link>
      {/* On a phone the menu scrolls sideways. */}
      <nav className="flex gap-1 h-full flex-1 min-w-0 ml-1 sm:ml-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {NAV.filter((i) => i.permission === null || hasPermission(permissions, i.permission)).map(
          (item) => {
            const active =
              location.pathname === item.path ||
              (item.path !== "/" && location.pathname.startsWith(item.path))
            return (
              <Link key={item.path} to={item.path}
                className={cn(
                  "text-[#B9C4D6] text-[12px] font-medium px-2 h-full flex items-center shrink-0 whitespace-nowrap border-b-2 border-transparent hover:text-white hover:bg-white/5",
                  active && "text-white border-gold-500 bg-white/5"
                )}>
                {item.name}
                {item.path === "/integrations" && erpBroken && (
                  <span className="ml-1.5 w-2 h-2 rounded-full bg-red-500" title="A connection needs attention" />
                )}
                {item.path === "/enquiries" && unowned > 0 && (
                  <span className="ml-1.5 rounded-full bg-amber-500 text-white text-[10px] font-bold px-1.5 leading-[16px]" title="Enquiries without an owner">{unowned}</span>
                )}
                {item.path === "/decisions" && waiting > 0 && (
                  <span className="ml-1.5 rounded-full bg-gold-500 text-ink-900 text-[10px] font-bold px-1.5 leading-[16px]">{waiting}</span>
                )}
              </Link>
            )
          }
        )}
      </nav>
      <button onClick={logout} title={`Log out ${me?.email ?? ""}`}
        className="shrink-0 w-[30px] h-[30px] rounded-full bg-teal-500 text-white grid place-items-center text-[12px] font-bold hover:bg-teal-600">
        {initials}
      </button>
    </header>
  )
}
