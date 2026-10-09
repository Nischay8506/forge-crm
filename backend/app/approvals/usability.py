"""Prepared cards for the approval-inbox usability test (decision H8 C).

F12.3's Done-When ("every card decidable in under 60 seconds") stays pending until
real people have decided real-looking cards: see docs/USABILITY_TEST.md. This builds a
separate company for one test session, with its own people, accounts, ERP figures,
policy rules and eleven waiting cards that show every part of a card: a single
approver, a two-step chain, each kind of recommendation, fresh and out-of-date ERP
figures, a card near its deadline, one past it, and one the tester asked for
themselves (so a second person must decide it).

    uv run python -m app.cli seed-usability --slug ut-anita
    uv run python -m app.cli seed-usability --slug ut-anita --refresh   # ERP read now

Everything here is pretend: the Sandbox Agent's payments touch only its own table,
and the ERP connections are paused, so Forge never calls them.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import runtime
from app.agents.registry import load_definitions
from app.approvals import sla
from app.config import get_settings
from app.core.security import hash_password
from app.db import set_tenant, tenant_session
from app.integrations.connections import _set_credentials
from app.models import (
    Decision,
    ErpConnection,
    ErpCustomer,
    ErpInvoice,
    PolicyRule,
    Role,
    SyncFlow,
    Tenant,
    User,
    UserRole,
)
from app.steward.records import create_account

ACTOR = "system:usability"
FRESH_ERP = "ERPNext Pune"
DECIDER = ["decisions.decide", "accounts.read", "steward.read", "agents.read",
           "integrations.read"]


@dataclass
class Seeded:
    slug: str
    password: str
    people: dict[str, str]  # email -> what they are for
    cards: int


# Accounts (made-up names), their tier, and ERP invoices: (days ago, days to due, total,
# still owed, status). None: not linked to an ERP.
ACCOUNTS = {
    "Sahyadri Pumps": ("key", FRESH_ERP, [(40, -10, 360000, 60000, "overdue"),
                                          (8, 22, 210000, 210000, "unpaid"),
                                          (120, -90, 480000, 0, "paid")]),
    "Meridian Foods": ("standard", None, []),
    "Narmada Castings": ("standard", "ERPNext Nashik", [(70, -40, 180000, 80000, "overdue"),
                                                        (20, 10, 95000, 95000, "unpaid")]),
    "Deccan Flow Controls": ("key", FRESH_ERP, [(15, 15, 650000, 650000, "unpaid"),
                                                (200, -170, 900000, 0, "paid")]),
    "Konkan Agro Processors": ("standard", None, []),
}

# The cards: (account, amount or None for a message, recommended option, why,
# requested by: "rep" or "tester", special: None, "late", "near").
CARDS = [
    ("Sahyadri Pumps", 150000, "approve",
     "Matches their PO 4471 and their bank transfer reference; they pay on time.", "rep", None),
    ("Meridian Foods", 240000, "approve",
     "Second instalment of the agreed schedule; the first was received on time.", "rep", None),
    ("Narmada Castings", 380000, "counter",
     "Counter at ₹3,00,000: ₹80,000 of this is disputed and has a credit note pending.",
     "rep", None),
    ("Deccan Flow Controls", 650000, "approve",
     "Clears invoice SI-2291 in full; the amount matches the invoice exactly.", "rep", None),
    ("Konkan Agro Processors", 120000, "reject",
     "Looks like a duplicate: the same ₹1,20,000 with the same reference was recorded "
     "yesterday.", "rep", None),
    ("Sahyadri Pumps", 200000, "approve",
     "Advance against the next order, as agreed in the signed quote.", "rep", "late"),
    ("Meridian Foods", 175000, "approve",
     "Final instalment; closes the account's open balance.", "rep", "near"),
    ("Narmada Castings", None, "approve",
     "A reminder about the overdue invoice, in the tone they replied to last time.", "rep",
     None),
    ("Narmada Castings", 900000, "counter",
     "Counter at ₹7,50,000: the remaining ₹1,50,000 waits for the quality report.", "rep",
     None),
    ("Konkan Agro Processors", 110000, "approve",
     "Recorded from the bank statement; the tester asked for it, so someone else decides.",
     "tester", None),
    ("Deccan Flow Controls", 130000, "approve",
     "Part payment the customer announced by email this morning.", "rep", None),
]


async def _tenant(slug: str, password: str) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """The company, its roles and its people (as the schema owner, like create-tenant)."""
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with async_sessionmaker(engine)() as s, s.begin():
            if await s.scalar(select(Tenant.id).where(Tenant.slug == slug)):
                raise SystemExit(f"A company with slug '{slug}' already exists; pick another "
                                 "--slug, or add --refresh to bring its ERP figures up to date")
            t = Tenant(name=f"Usability test ({slug})", slug=slug)
            s.add(t)
            await s.flush()
            await set_tenant(s, t.id)
            roles = {"Admin": Role(tenant_id=t.id, name="Admin", description="Full access",
                                   permissions=["*"]),
                     "Sales Manager": Role(tenant_id=t.id, name="Sales Manager",
                                           description="Decides payments and messages",
                                           permissions=DECIDER),
                     "Finance": Role(tenant_id=t.id, name="Finance",
                                     description="Second approver on large amounts",
                                     permissions=DECIDER),
                     "Sales": Role(tenant_id=t.id, name="Sales",
                                   description="Asks for payments to be recorded",
                                   permissions=["accounts.read"])}
            s.add_all(roles.values())
            await s.flush()
            people = {"admin": ("Usability Admin", "Admin"),
                      "tester": ("Test Participant", "Sales Manager"),
                      "finance": ("Rahul Finance", "Finance"),
                      "rep": ("Vikram Sales", "Sales")}
            ids: dict[str, uuid.UUID] = {}
            for key, (name, role) in people.items():
                u = User(tenant_id=t.id, email=f"{key}@{slug}.example.com", full_name=name,
                         password_hash=hash_password(password))
                s.add(u)
                await s.flush()
                s.add(UserRole(tenant_id=t.id, user_id=u.id, role_id=roles[role].id))
                ids[key] = u.id
            ids.update({f"role:{k}": r.id for k, r in roles.items()})
            return t.id, ids
    finally:
        await engine.dispose()


async def _erp(tid: uuid.UUID, accounts: dict[str, uuid.UUID]) -> None:
    today, now = date.today(), datetime.now(UTC)
    async with tenant_session(tid) as s:
        conns: dict[str, ErpConnection] = {}
        for name, read_ago in ((FRESH_ERP, timedelta(minutes=2)),
                               ("ERPNext Nashik", timedelta(hours=5))):
            c = ErpConnection(id=uuid.uuid4(), tenant_id=tid, kind="erpnext", name=name,
                              base_url="https://erp.invalid", status="paused",
                              created_by=ACTOR, updated_by=ACTOR)
            _set_credentials(c, "usability-test-key", "usability-test-secret")
            s.add(c)
            await s.flush()
            s.add(SyncFlow(tenant_id=tid, connection_id=c.id, entity="invoice", enabled=False,
                           last_success_at=now - read_ago))
            conns[name] = c
        for n, (acc_name, (_, erp, invoices)) in enumerate(ACCOUNTS.items()):
            if erp is None:
                continue
            c, ext = conns[erp], f"CUST-{n + 1:03d}"
            s.add(ErpCustomer(tenant_id=tid, connection_id=c.id, external_id=ext,
                              modified_at=now, data={}, name=acc_name,
                              account_id=accounts[acc_name], link_status="linked"))
            for i, (ago, due_in, total, owed, status) in enumerate(invoices):
                s.add(ErpInvoice(tenant_id=tid, connection_id=c.id,
                                 external_id=f"{ext}-INV-{i + 1}", modified_at=now, data={},
                                 number=f"SI-{2200 + n * 10 + i}", customer_external_id=ext,
                                 invoice_date=today - timedelta(days=ago),
                                 due_date=today + timedelta(days=due_in), status=status,
                                 currency="INR", grand_total=Decimal(total),
                                 outstanding=Decimal(owed)))


async def refresh(slug: str) -> None:
    """Make the fresh ERP connection's figures current again (they count as out of date
    15 minutes after their last read)."""
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with async_sessionmaker(engine)() as s, s.begin():
            tid = await s.scalar(select(Tenant.id).where(Tenant.slug == slug))
            if tid is None:
                raise SystemExit(f"No company with slug '{slug}'")
    finally:
        await engine.dispose()
    async with tenant_session(tid) as s:
        conn = await s.scalar(select(ErpConnection.id).where(ErpConnection.name == FRESH_ERP))
        await s.execute(update(SyncFlow).where(SyncFlow.connection_id == conn)
                        .values(last_success_at=datetime.now(UTC)))


async def seed(slug: str, password: str) -> Seeded:
    load_definitions()
    slug = slug.lower()
    tid, ids = await _tenant(slug, password)
    async with tenant_session(tid) as s:
        accounts = {}
        for name, (tier, _, _) in ACCOUNTS.items():
            acc = await create_account(s, tid, {"name": name, "tier": tier, "city": "Pune"},
                                       source="usability_seed", actor=ACTOR,
                                       publish_event=False)
            accounts[name] = acc.id
        # The rules the cards come from (H1, H4): above ₹1,00,000 the Sales Manager
        # decides within 4 working hours; above ₹5,00,000 Finance approves after them.
        sm, fin = str(ids["role:Sales Manager"]), str(ids["role:Finance"])
        s.add_all([
            PolicyRule(tenant_id=tid, agent_key="sandbox", tool_key="sandbox.record_payment",
                       conditions=[{"fact": "amount", "op": "gt", "value": 100000}],
                       outcome="ask_person", approver_role_id=ids["role:Sales Manager"],
                       approval_steps=[sm], sla_hours=Decimal(4), created_by=ACTOR,
                       updated_by=ACTOR),
            PolicyRule(tenant_id=tid, agent_key="sandbox", tool_key="sandbox.record_payment",
                       conditions=[{"fact": "amount", "op": "gt", "value": 500000}],
                       outcome="ask_person", approver_role_id=ids["role:Sales Manager"],
                       approval_steps=[sm, fin], sla_hours=Decimal(8), created_by=ACTOR,
                       updated_by=ACTOR),
            PolicyRule(tenant_id=tid, agent_key="sandbox", tool_key="sandbox.draft_message",
                       outcome="ask_person", approver_role_id=ids["role:Sales Manager"],
                       approval_steps=[sm], sla_hours=Decimal(2), created_by=ACTOR,
                       updated_by=ACTOR),
        ])
    await _erp(tid, accounts)

    made: list[tuple[uuid.UUID, str | None]] = []
    for acc, amount, option, why, who, special in CARDS:
        async with tenant_session(tid) as s:
            ctx = await runtime.start_run(s, tid, "sandbox", "try_it", ACTOR)
            if amount is None:
                tool, args = "sandbox.draft_message", {"account_id": str(accounts[acc]),
                                                       "channel": "whatsapp"}
            else:
                tool, args = "sandbox.record_payment", {"account_id": str(accounts[acc]),
                                                        "amount": str(amount)}
            out = await runtime.call_tool(s, ctx, tool, args, recommendation=why,
                                          recommend=option,
                                          requested_by=f"user:{ids[who]}")
            await runtime.finish_run(s, ctx, summary={"status": out.status})
            if out.decision_id is None:
                raise RuntimeError(f"Expected a card for {acc} {amount}, got {out.status}")
            made.append((out.decision_id, special))

    # One card past its deadline and one near it, then the deadline job runs as it would
    # every minute: a reminder for the near one, an escalation for the late one.
    now = datetime.now(UTC)
    async with tenant_session(tid) as s:
        for did, special in made:
            if special is None:
                continue
            step = (await s.get(Decision, did)).current_step
            late = special == "late"
            step.started_at = now - (timedelta(hours=6) if late else timedelta(minutes=220))
            step.remind_at = now - (timedelta(hours=3) if late else timedelta(minutes=40))
            step.due_at = now + (timedelta(minutes=-40) if late else timedelta(minutes=20))
        await s.flush()
        await sla.tick_tenant(s, tid, now, bottlenecks=False)

    return Seeded(slug=slug, password=password, cards=len(made), people={
        f"tester@{slug}.example.com": "the participant (Sales Manager): decides the cards",
        f"finance@{slug}.example.com": "Finance: the second step on amounts above ₹5,00,000",
        f"admin@{slug}.example.com": "the facilitator: reads the results on Approval numbers",
        f"rep@{slug}.example.com": "Sales: asked for the payments (cannot decide)"})
