import { Link, NavLink, Outlet } from "react-router-dom"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { useOverview } from "./hooks"

const TABS = [
  { to: "/integrations/connections", label: "Connections", permission: null },
  { to: "/integrations/customers", label: "Customers", permission: null },
  { to: "/integrations/codes", label: "Code lists", permission: null },
  { to: "/integrations/mcp", label: "Outside AI tools", permission: "mcp.manage" },
]

export function IntegrationsLayout() {
  const perms = usePermissions()
  const { data } = useOverview()
  const broken = (data?.connections ?? []).filter((c) => c.health === "problem")

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between border-b border-line">
        <h1 className="text-2xl font-bold text-ink-900 pb-3">Integrations</h1>
        <nav className="flex gap-1">
          {TABS.filter((t) => t.permission === null || hasPermission(perms, t.permission)).map((t) => (
            <NavLink key={t.to} to={t.to}
              className={({ isActive }) => cn(
                "px-3 py-2 text-[13px] font-medium text-ink-500 border-b-2 border-transparent -mb-px hover:text-ink-900",
                isActive && "text-ink-900 border-teal-500")}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </div>
      {data && !data.credential_keys_configured && (
        <div className="rounded-md bg-amber-100 text-ink-700 text-[13px] p-3">
          <b>Forge cannot store ERP logins yet.</b> An administrator sets <code>CREDENTIAL_KEYS</code> in
          backend/.env (run <code>uv run python -m app.cli new-credential-key</code> and paste the line it prints), then
          restarts the backend and worker.
        </div>
      )}
      {broken.map((c) => (
        <div key={c.id} role="alert"
          className="rounded-md bg-red-100 text-red-500 text-[13px] p-3 flex items-center justify-between gap-3">
          <span>
            <b>{c.name}: {c.problems[0]?.title ?? "needs attention"}.</b>{" "}
            <span className="text-ink-700">{c.problems[0]?.fix ?? c.problems[0]?.detail ?? ""}</span>
          </span>
          <Link to={`/integrations/connections/${c.id}`} className="font-semibold underline shrink-0">Open</Link>
        </div>
      ))}
      <Outlet />
    </div>
  )
}
