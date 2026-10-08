"""Permission catalogue. Every endpoint declares the permission it needs with
`require_permission(...)`; roles hold lists of these codes ("*" = all).
Add new codes here as modules are built so the admin screen can list them."""

PERMISSIONS: dict[str, str] = {
    "users.read": "View users and their roles",
    "users.manage": "Create, deactivate and assign roles to users",
    "roles.manage": "Create and edit roles",
    "llm.usage.read": "View LLM usage and cost",
    # F12.1 Golden Record & Data Steward
    "accounts.read": "View accounts (companies, sites) and contacts",
    "accounts.write": "Create and edit accounts and contacts",
    "steward.read": "View duplicate candidates, merge history and data hygiene",
    "steward.review": "Decide duplicate candidates and enrichment proposals",
    "steward.undo": "Undo a merge",
    "steward.import": "Bulk-import legacy accounts and contacts",
    # F12.2 Agent runtime, policy gate, autonomy; F12.4 audit chain (minimal)
    "agents.read": "View agents, what they can do, their levels, policy rules and stops",
    "agents.manage": "Change autonomy levels, policy rules and kill-switches",
    "decisions.decide": "See what agents are waiting on, and approve or reject it",
    "audit.read": "View and verify the audit log",
    "records.takeover": "Take a record over from agents and hand it back",
}

ALL = "*"


def has_permission(granted: set[str], required: str) -> bool:
    return ALL in granted or required in granted
