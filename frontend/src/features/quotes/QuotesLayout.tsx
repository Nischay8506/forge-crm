import { NavLink, Outlet } from "react-router-dom"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"

const TABS = [
  { to: "/quotes", label: "Quotes", permission: "quotes.read", end: true },
  { to: "/quotes/materials", label: "Material rates", permission: "costs.view" },
  { to: "/quotes/machines", label: "Machine-hour rates", permission: "costs.view" },
  { to: "/quotes/cost-models", label: "Cost models", permission: "costs.view" },
  { to: "/quotes/price-policies", label: "Pricing policy", permission: "costs.view" },
  { to: "/quotes/approval-rules", label: "Approval rules", permission: "quotes.read" },
]

export function QuotesLayout() {
  const perms = usePermissions()
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-ink-900">Quotes</h1>
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
