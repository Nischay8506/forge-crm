import { useSearchParams } from "react-router-dom"
import { hasPermission } from "@/lib/permissions"
import { cn } from "@/lib/utils"
import { usePermissions } from "@/stores/authStore"
import { CapabilitySetup } from "./CapabilitySetup"
import { FamiliesSetup } from "./FamiliesSetup"
import { MailboxSetup } from "./MailboxSetup"
import { RoutingSetup } from "./RoutingSetup"
import { ScoringSetup } from "./ScoringSetup"
import { TimersSetup } from "./TimersSetup"

const TABS = [
  { key: "mailbox", label: "Mailbox" },
  { key: "routing", label: "Owners" },
  { key: "timers", label: "Timers and new mail" },
  { key: "families", label: "Product families" },
  { key: "capability", label: "What we make" },
  { key: "scoring", label: "Scoring" },
] as const

export function SetupPage() {
  const canManage = hasPermission(usePermissions(), "enquiries.manage")
  const [search, setSearch] = useSearchParams()
  const tab = TABS.find((t) => t.key === search.get("tab"))?.key ?? "mailbox"
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button key={t.key} type="button" onClick={() => setSearch({ tab: t.key }, { replace: true })}
            className={cn("rounded-full border px-3 py-1 text-[12.5px]",
              t.key === tab ? "border-teal-500 bg-teal-100 text-ink-900" : "border-line text-ink-500 hover:text-ink-900")}>
            {t.label}
          </button>
        ))}
      </div>
      {!canManage && <p className="text-[12.5px] text-ink-500">You can look; changing the setup needs the permission to manage enquiries.</p>}
      {tab === "mailbox" && <MailboxSetup canManage={canManage} notice={{ connected: search.get("connected"), error: search.get("error") }} />}
      {tab === "routing" && <RoutingSetup canManage={canManage} />}
      {tab === "timers" && <TimersSetup canManage={canManage} />}
      {tab === "families" && <FamiliesSetup canManage={canManage} />}
      {tab === "capability" && <CapabilitySetup canManage={canManage} />}
      {tab === "scoring" && <ScoringSetup canManage={canManage} />}
    </div>
  )
}
