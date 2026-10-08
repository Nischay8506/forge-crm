import { NavLink, Outlet } from "react-router-dom"
import { cn } from "@/lib/utils"

const TABS = [
  { to: "/steward/review", label: "Review queue" },
  { to: "/steward/merges", label: "Merge history" },
  { to: "/steward/imports", label: "Imports" },
  { to: "/steward/hygiene", label: "Data hygiene" },
]

export function StewardLayout() {
  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between border-b border-line">
        <h1 className="text-2xl font-bold text-ink-900 pb-3">Data Steward</h1>
        <nav className="flex gap-1">
          {TABS.map((t) => (
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
