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
    "decisions.decide": "See what agents are waiting on, and approve, reject, counter or hand "
                        "it on",
    # F12.3 Human approval inbox (Step 5)
    "decisions.manage": "Change the working calendar, deadlines and alert limits of the "
                        "approval inbox, and see everyone's approval analytics",
    "audit.read": "View and verify the audit log",
    # F12.4 audit replay, redaction and retention (decisions R4-R6)
    "audit.personal_data": "See personal data in the audit trail (one replay at a time, "
                           "after typing a reason; each reveal is recorded)",
    "audit.manage": "Change retention periods and place or lift legal holds",
    "privacy.erase": "Ask for a contact's personal data to be erased (a second person "
                     "approves)",
    "records.takeover": "Take a record over from agents and hand it back",
    # F12.6 Integration framework and ERP adapters
    "integrations.read": "View ERP connections, sync health, alerts and synced ERP data",
    "integrations.manage": "Connect ERPs, change their credentials and settings, run syncs",
    "integrations.map": "Match ERP customers to accounts and map ERP codes in the mapping "
                        "studio, and retry or skip records that failed to sync",
    "erp.write": "Ask for an account to be created as a customer in an ERP",
    "mcp.manage": "Create and revoke tokens for outside AI tools (MCP)",
    # F12.5 Multilingual & channel fabric (Step 6)
    "messages.send": "Write and send messages to customers, send a held message anyway, and "
                     "act as the customer in the Test inbox",
    "channels.manage": "Set up the email account, sending limits and quiet hours, and switch "
                       "email between the Test inbox and live sending",
    "languages.manage": "Change the tone guides and the glossary",
    "languages.review": "Review the writing of AI-written messages (for native speakers)",
    # Phase 2, M1 Enquiry (F1.1-F1.10)
    "enquiries.read": "See the enquiry queue, enquiries, their emails and Other mail",
    "enquiries.work": "Work on enquiries: correct what was read, acknowledge, mark the quote "
                      "sent, close, say if two are the same, add manual enquiries, sort Other "
                      "mail and use the Test mailbox",
    "enquiries.assign": "Give an enquiry to someone else (with a reason)",
    "enquiries.manage": "Connect the sales mailbox and set routing, timers, product families "
                        "and who takes enquiries",
    # Phase 2, M3 CPQ (F3.1-F3.9)
    "products.configure": "Use the product configurator and see each family's options and "
                          "rules",
    "products.manage": "Set up a product family's options, rules and variant code pattern",
    "quotes.read": "See quotes and their revisions",
    "quotes.work": "Start quotes from enquiries and change their draft lines",
    # Decision C17 B: everyone in sales sees costs, as a permission that can change.
    "costs.view": "See what each quote line costs and why (the cost breakdown)",
    "costs.manage": "Set material and machine-hour rates and each family's cost model",
    "prices.manage": "Set each family's target margin, floor, tier discounts and quantity "
                     "breaks",
    "prices.approve": "Approve a quote line priced below its floor",
    "discounts.approve": "Sign off a quote's discount, where the delegation of "
                         "authority names your role",
    "approvals.manage": "Set who has to approve a discount, and in what order "
                        "(the delegation of authority)",
}

ALL = "*"


def has_permission(granted: set[str], required: str) -> bool:
    return ALL in granted or required in granted
