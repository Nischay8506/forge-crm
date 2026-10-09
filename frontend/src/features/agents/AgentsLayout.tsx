import { NavLink, Outlet } from "react-router-dom"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"

const TABS = [
  { to: "/agents/list", label: "Agents", permission: "agents.read" },
  { to: "/agents/policies", label: "Policy rules", permission: "agents.read" },
  { to: "/agents/stops", label: "Stops", permission: "agents.read" },
  { to: "/agents/runs", label: "Runs", permission: "audit.read" },
  { to: "/agents/audit", label: "Audit log", permission: "audit.read" },
  { to: "/agents/retention", label: "Retention", permission: "audit.read" },
]

export function AgentsLayout() {
  const perms = usePermissions()
  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between border-b border-line">
        <h1 className="text-2xl font-bold text-ink-900 pb-3">Agents</h1>
        <nav className="flex gap-1">
          {TABS.filter((t) => hasPermission(perms, t.permission)).map((t) => (
            <NavLink key={t.to} to={t.to}
              className={({ isActive }) => cn(
                "px-3 py-2 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900",
                isActive && "text-ink-900 border-teal-500")}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </div>
      <Outlet />
    </div>
  )
}
