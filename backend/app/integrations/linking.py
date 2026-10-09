"""Joining ERP customers to Forge accounts (decisions E4, E5; safeguards 2 and 3).

Matching uses the Data Steward's own comparison (app/steward/matching.py) and only
links on its own when a verified identifier settles it:
- exactly one active account shares a valid GSTIN, PAN or CIN with the ERP customer
  (the steward's auto-merge band with a same-PAN or same-CIN signal), and their
  GSTINs do not differ (same PAN, other GSTIN is another registration: a person
  decides) -> link, through the policy gate;
- any other possible match -> review: a person picks the account in the mapping
  studio (a task card in "Waiting for you" says how many are waiting);
- no possible match at all -> a new account marked as coming from the ERP, through
  the policy gate. A customer disabled in the ERP is not turned into a new account.

Field ownership (E5): the ERP owns legal and money fields. Legal name, GSTIN, PAN
and billing address are brought onto the account with provenance pointing at the
ERP record; credit limit and payment terms stay with each connection
(erp_customers), since two ERPs may set them differently. A value only replaces an
existing one when it came from the same ERP record or from Forge itself; a different
GSTIN or PAN, or a value that another ERP record set (safeguard 3), becomes an
approval card instead of being applied.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AGENTS, AgentContext
from app.agents.runtime import call_tool, finish_run, start_run, supersede
from app.audit import chain
from app.core.errors import AppError, NotFound
from app.integrations.alerts import sync_task
from app.models import Account, Decision, ErpConnection, ErpCustomer
from app.steward import identifiers as ids
from app.steward.candidates import account_rec, neighbours_for_account
from app.steward.matching import AUTO_MERGE, DISTINCT, AccountRec, compare_accounts
from app.steward.normalize import phonetic_keys
from app.steward.records import current_provenance, update_account

AGENT_KEY = "erp_connector"
LINK_TOOL = "erp.link_customer"
NEW_ACCOUNT_TOOL = "erp.new_account"
UPDATE_TOOL = "erp.update_account"
CREATE_TOOL = "erp.create_customer"

ADDRESS = ("address_line1", "address_line2", "city", "state", "pincode")
# Groups of ERP-owned account fields, applied or reviewed together.
GROUPS: dict[str, tuple[str, ...]] = {
    "legal_name": ("legal_name",), "gstin": ("gstin",), "pan": ("pan",), "address": ADDRESS,
}
GROUP_LABELS = {"legal_name": "legal name", "gstin": "GSTIN", "pan": "PAN",
                "address": "billing address"}
IDENTITY_GROUPS = frozenset({"gstin", "pan"})
VERIFIED_SIGNALS = frozenset({"same_pan", "same_cin"})


def source_ref(conn: ErpConnection | uuid.UUID, ec: ErpCustomer) -> str:
    """Provenance reference for values from one ERP record: erp:<connection>:<code>."""
    conn_id = conn.id if isinstance(conn, ErpConnection) else conn
    return f"erp:{conn_id}:{ec.external_id}"


# --- what the ERP says -------------------------------------------------------------------

def erp_values(ec: ErpCustomer) -> dict[str, Any]:
    """The ERP-owned account fields this customer carries, normalised like Forge's own
    and leaving out anything that fails its check (an invalid GSTIN is never copied)."""
    d = ec.data or {}
    out: dict[str, Any] = {}
    g = ids.check_gstin(ec.gstin)
    if ec.gstin and g.valid:
        out["gstin"] = g.value
    pan = ec.pan or (ids.pan_from_gstin(out["gstin"]) if out.get("gstin") else None)
    if pan and ids.check_pan(pan).valid:
        out["pan"] = ids.check_pan(pan).value
    if d.get("legal_name"):
        out["legal_name"] = d["legal_name"].strip()
    addr = d.get("billing_address") or {}
    if addr.get("line1"):
        out["address_line1"] = addr["line1"].strip()
        out["address_line2"] = (addr.get("line2") or "").strip() or None
        out["city"] = (addr.get("city") or "").strip() or None
        out["state"] = (addr.get("state") or "").strip() or None
        out["pincode"] = ids.normalize_pincode(addr.get("pincode"))
    return out


def new_account_values(ec: ErpCustomer) -> dict[str, Any]:
    d = ec.data or {}
    return {"kind": "entity", "name": ec.name, "email": d.get("email"), "phone": d.get("phone"),
            "website": d.get("website"), **erp_values(ec)}


def _rec(ec: ErpCustomer) -> AccountRec:
    v = erp_values(ec)
    d = ec.data or {}
    address = " ".join(x for x in (v.get("address_line1"), v.get("address_line2"),
                                   v.get("city")) if x)
    return AccountRec(id=None, kind="entity", name=ec.name, gstin=v.get("gstin"), pan=v.get("pan"),
                      domain=ids.normalize_domain(d.get("website"))
                      or ids.normalize_domain(d.get("email")),
                      phone=ids.normalize_phone(d["phone"]) if d.get("phone") else None,
                      pincode=v.get("pincode"), city=v.get("city"), address=address or None)


# --- deciding what to do with one customer -------------------------------------------------

@dataclass
class Plan:
    kind: str  # link | new | review | skip
    account_id: uuid.UUID | None = None
    method: str | None = None
    score: float | None = None
    reasons: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)


async def plan(session: AsyncSession, ec: ErpCustomer) -> Plan:
    rec = _rec(ec)
    found = []
    for acc in await neighbours_for_account(session, rec, phonetic_keys(ec.name), None):
        res = compare_accounts(rec, account_rec(acc))
        if res.band != DISTINCT:
            found.append((acc, res))
    found.sort(key=lambda x: -x[1].score)
    candidates = [{
        "account_id": str(acc.id), "name": acc.name, "gstin": acc.gstin, "city": acc.city,
        "band": res.band, "score": res.score,
        "reasons": [r["detail"] for r in res.reasons if r["effect"] != "info"],
    } for acc, res in found[:5]]
    verified = [(acc, res) for acc, res in found if res.band == AUTO_MERGE
                and VERIFIED_SIGNALS & {r["signal"] for r in res.reasons}]
    if len(verified) == 1:
        acc, res = verified[0]
        if not (acc.gstin and rec.gstin and acc.gstin != rec.gstin):
            method = ("gstin" if rec.gstin and acc.gstin == rec.gstin else
                      "pan" if "same_pan" in {r["signal"] for r in res.reasons} else "cin")
            return Plan("link", acc.id, method, res.score,
                        [r["detail"] for r in res.reasons if r["effect"] != "info"], candidates)
        return Plan("review", reasons=[
            f"Same PAN as {acc.name} but a different GSTIN ({rec.gstin} vs {acc.gstin}): "
            "possibly another registration of the same company"], candidates=candidates)
    if len(verified) > 1:
        return Plan("review", reasons=[f"{len(verified)} accounts share a verified ID with this "
                                       "customer"], candidates=candidates)
    if found:
        return Plan("review", reasons=["Possible match without a verified ID (GSTIN, PAN or "
                                       "CIN)"], candidates=candidates)
    if ec.disabled:
        return Plan("skip", reasons=["Disabled in the ERP and matches no account"])
    return Plan("new", reasons=["No account looks like this customer"])


# --- applying ERP-owned fields ---------------------------------------------------------------

@dataclass
class FieldChanges:
    apply: dict[str, Any] = field(default_factory=dict)
    review: dict[str, dict[str, Any]] = field(default_factory=dict)  # group -> changes
    why: dict[str, str] = field(default_factory=dict)


async def field_changes(session: AsyncSession, conn_id: uuid.UUID, ec: ErpCustomer,
                        acc: Account) -> FieldChanges:
    values = erp_values(ec)
    prov = await current_provenance(session, acc.id)
    mine = source_ref(conn_id, ec)
    out = FieldChanges()
    for group, fields in GROUPS.items():
        changes = {f: values.get(f) for f in fields
                   if f in values and getattr(acc, f) != values.get(f)}
        if group == "address" and changes:
            changes = {f: values.get(f) for f in fields}  # the address moves as a whole
        if not changes:
            continue
        filled = [f for f in fields if getattr(acc, f) not in (None, "")]
        if not filled:
            out.apply.update(changes)  # filling empty fields never contradicts anything
            continue
        others = sorted({p.source_ref or "" for f in filled if (p := prov.get(f)) is not None
                         and p.source == "erp" and p.source_ref != mine})
        if group in IDENTITY_GROUPS:
            out.review[group] = changes
            out.why[group] = (f"The account's {GROUP_LABELS[group]} is {getattr(acc, group)}; "
                              f"the ERP says {values[group]}. Different tax IDs usually mean "
                              "a different legal entity or a typo, so a person decides.")
        elif others:
            out.review[group] = changes
            out.why[group] = (f"Another ERP record ({others[0].split(':')[-1]}) set the "
                              f"account's {GROUP_LABELS[group]}; two ERPs disagree, so a "
                              "person decides which one is right.")
        else:
            out.apply.update(changes)
    return out


async def _review_card(session: AsyncSession, ctx: AgentContext, conn: ErpConnection,
                       ec: ErpCustomer, acc: Account, group: str, changes: dict[str, Any],
                       why: str) -> None:
    """An approval card for one contradicted field group (forced: whatever the agent's
    level). Approving runs erp.update_account through the gate."""
    key = f"erp.field:{acc.id}:{group}:{ec.id}"
    existing = await session.scalar(select(Decision).where(
        Decision.dedupe_key == key, Decision.status == "pending"))
    args = {"erp_customer_id": str(ec.id), "account_id": str(acc.id), "changes": changes}
    if existing is not None:
        if existing.args == args:
            return
        await supersede(session, key, ctx.actor, "The ERP record changed again")
    label = GROUP_LABELS[group]
    now_value = ", ".join(str(getattr(acc, f)) for f in GROUPS[group] if getattr(acc, f))
    new_value = ", ".join(str(v) for v in changes.values() if v)
    d = Decision(tenant_id=acc.tenant_id, kind="approval", status="pending",
                 agent_key=AGENT_KEY, tool_key=UPDATE_TOOL, run_id=ctx.run_id,
                 title=f"Change the {label} of {acc.name} to {new_value} (from {conn.name})"[:300],
                 recommendation=f"Check which {label} is right before approving; rejecting "
                                f"keeps {now_value or 'the current value'}.",
                 reasons=[why], args=args, facts={},
                 checks=[{"check": "review", "ok": False, "detail": why}],
                 record_type="account", record_id=acc.id, dedupe_key=key)
    session.add(d)
    await session.flush()
    await chain.append(session, acc.tenant_id, "decision.requested", actor=ctx.actor,
                       agent_key=AGENT_KEY, run_id=ctx.run_id, record_type="account",
                       record_id=acc.id, data={"decision_id": d.id, "tool": UPDATE_TOOL,
                                               "reasons": [why], "erp_customer": ec.external_id,
                                               "connection": conn.name})


async def bring_fields_over(session: AsyncSession, ctx: AgentContext | None, conn: ErpConnection,
                            ec: ErpCustomer, acc: Account, *, actor: str,
                            gated: bool) -> dict[str, Any]:
    """Apply the ERP-owned fields that do not contradict anything and raise approval
    cards for the rest. gated=False when the caller already went through the gate (a
    link tool run) or is a person acting directly."""
    if acc.status != "active":
        return {"applied": [], "review": []}
    ch = await field_changes(session, conn.id, ec, acc)
    applied: list[str] = []
    if ch.apply:
        if gated:
            assert ctx is not None
            outcome = await call_tool(session, ctx, UPDATE_TOOL, {
                "erp_customer_id": ec.id, "account_id": acc.id, "changes": ch.apply})
            if outcome.status == "done":
                applied = sorted(ch.apply)
        else:
            await update_account(session, acc, ch.apply, source="erp", actor=actor, strict=False,
                                 source_ref=source_ref(conn, ec))
            applied = sorted(ch.apply)
    if ch.review:
        rctx = ctx or AgentContext(tenant_id=acc.tenant_id, agent=AGENTS[AGENT_KEY])
        for group, changes in ch.review.items():
            await _review_card(session, rctx, conn, ec, acc, group, changes, ch.why[group])
    return {"applied": applied, "review": sorted(ch.review)}


# --- running over changed customers ------------------------------------------------------------

def _set_link(ec: ErpCustomer, account_id: uuid.UUID | None, status: str, method: str | None,
              actor: str, reasons: list[str] | None = None) -> None:
    ec.account_id, ec.link_status, ec.link_method = account_id, status, method
    ec.link_reasons = reasons or []
    ec.linked_by, ec.linked_at = (actor, datetime.now(UTC)) if account_id else (None, None)
    if account_id:
        ec.candidates = []


async def process_changed(session: AsyncSession, conn: ErpConnection,
                          erp_customer_ids: list[uuid.UUID], *, trigger: str) -> dict[str, int]:
    """After a sync: link new customers, refresh linked accounts' ERP-owned fields."""
    counts = {"linked": 0, "new_accounts": 0, "review": 0, "waiting": 0, "updated": 0,
              "skipped": 0}
    if not erp_customer_ids:
        return counts
    ctx = await start_run(session, conn.tenant_id, AGENT_KEY, trigger, conn.name)
    rows = list(await session.scalars(select(ErpCustomer).where(
        ErpCustomer.id.in_(erp_customer_ids)).order_by(ErpCustomer.external_id)))
    for ec in rows:
        if ec.link_status == "ignored":
            continue
        if ec.link_status == "linked" and ec.account_id:
            acc = await session.get(Account, ec.account_id)
            if acc is not None:
                r = await bring_fields_over(session, ctx, conn, ec, acc, actor=ctx.actor,
                                            gated=True)
                counts["updated"] += bool(r["applied"])
                counts["review"] += len(r["review"])
            continue
        if ec.link_method == "unlinked":
            continue  # a person unlinked it: only a person links it again
        p = await plan(session, ec)
        if p.kind == "link":
            outcome = await call_tool(session, ctx, LINK_TOOL, {
                "erp_customer_id": ec.id, "account_id": p.account_id, "method": p.method,
                "score": p.score, "reasons": p.reasons},
                recommendation="Link: they share a verified tax ID",
                dedupe_key=f"erp.link:{ec.id}")
            counts["linked" if outcome.status == "done" else "waiting"] += 1
            if outcome.status not in ("done", "waiting"):
                ec.link_status, ec.link_reasons = "review", outcome.reasons
                ec.candidates = p.candidates
        elif p.kind == "new":
            outcome = await call_tool(session, ctx, NEW_ACCOUNT_TOOL, {"erp_customer_id": ec.id},
                                      dedupe_key=f"erp.new:{ec.id}")
            counts["new_accounts" if outcome.status == "done" else "waiting"] += 1
        elif p.kind == "review":
            ec.link_status, ec.link_method = "review", None
            ec.link_reasons, ec.candidates = p.reasons, p.candidates
            counts["review"] += 1
        else:
            ec.link_status, ec.link_method, ec.link_reasons = "pending", "disabled", p.reasons
            counts["skipped"] += 1
    await finish_run(session, ctx, summary=counts)
    await refresh_review_task(session, conn)
    return counts


async def refresh_review_task(session: AsyncSession, conn: ErpConnection) -> None:
    n = await session.scalar(select(func.count()).select_from(ErpCustomer).where(
        ErpCustomer.connection_id == conn.id, ErpCustomer.link_status == "review",
        ErpCustomer.gone_at.is_(None))) or 0
    await sync_task(
        session, conn.tenant_id, key=f"review:{conn.id}", record_type="erp_connection",
        record_id=conn.id,
        title=(f"{n} customer{'s' if n != 1 else ''} from {conn.name} need matching to Forge "
               "accounts") if n else None,
        reasons=["The match is not certain (no shared verified ID, or several possible "
                 "accounts). Pick the account, create a new one, or ignore the customer in "
                 "Integrations > Customers."],
        args={"connection_id": str(conn.id), "waiting": n},
        done_note="Every customer is linked or ignored")


# --- tool bodies (registered in app/integrations/agent.py) -----------------------------------

async def link(session: AsyncSession, ec_id: uuid.UUID, account_id: uuid.UUID, *, method: str,
               actor: str, reasons: list[str], ctx: AgentContext | None) -> dict[str, Any]:
    ec = await session.get(ErpCustomer, ec_id, with_for_update=True)
    acc = await session.get(Account, account_id)
    if ec is None:
        raise NotFound("ERP customer not found")
    if acc is None or acc.status != "active":
        raise AppError("That account is not active (merged or inactive); pick the one it was "
                       "merged into")
    conn = await session.get(ErpConnection, ec.connection_id)
    previous = ec.account_id
    _set_link(ec, acc.id, "linked", method, actor, reasons)
    fields = await bring_fields_over(session, ctx, conn, ec, acc, actor=actor, gated=False)
    await refresh_review_task(session, conn)
    return {"erp_customer": ec.external_id, "connection": conn.name, "account_id": str(acc.id),
            "account": acc.name, "method": method,
            "previous_account_id": str(previous) if previous else None, **fields}


async def new_account(session: AsyncSession, ec_id: uuid.UUID, *, actor: str,
                      ctx: AgentContext | None) -> dict[str, Any]:
    from app.steward.records import create_account

    ec = await session.get(ErpCustomer, ec_id, with_for_update=True)
    if ec is None:
        raise NotFound("ERP customer not found")
    if ec.account_id:
        raise AppError("This ERP customer is already linked to an account")
    conn = await session.get(ErpConnection, ec.connection_id)
    acc = await create_account(session, ec.tenant_id, new_account_values(ec), source="erp",
                               actor=actor, strict=False, source_ref=source_ref(conn, ec))
    _set_link(ec, acc.id, "linked", "new_account", actor,
              [f"Created from {conn.name} customer {ec.external_id}"])
    await refresh_review_task(session, conn)
    return {"erp_customer": ec.external_id, "connection": conn.name, "account_id": str(acc.id),
            "account": acc.name}


async def update_from_erp(session: AsyncSession, ec_id: uuid.UUID, account_id: uuid.UUID,
                          changes: dict[str, Any], *, actor: str) -> dict[str, Any]:
    ec = await session.get(ErpCustomer, ec_id)
    acc = await session.get(Account, account_id)
    if ec is None or acc is None:
        raise NotFound("ERP customer or account not found")
    allowed = {f for fields in GROUPS.values() for f in fields}
    if set(changes) - allowed:
        raise AppError(f"Only ERP-owned fields can be updated from the ERP: {sorted(allowed)}")
    conn = await session.get(ErpConnection, ec.connection_id)
    await update_account(session, acc, changes, source="erp", actor=actor, strict=False,
                         source_ref=source_ref(conn, ec))
    return {"account_id": str(acc.id), "fields": sorted(changes), "from": f"{conn.name} "
            f"customer {ec.external_id}"}


# --- what a person does in the mapping studio (manual overrides, all audited) --------------

async def _manual(session: AsyncSession, ec: ErpCustomer, kind: str, actor: str,
                  data: dict[str, Any]) -> None:
    await chain.append(session, ec.tenant_id, kind, actor=actor, agent_key=None,
                       record_type="account" if data.get("account_id") else None,
                       record_id=uuid.UUID(data["account_id"]) if data.get("account_id")
                       else None,
                       data={"erp_customer_id": str(ec.id), "erp_customer": ec.external_id,
                             **data})


async def link_by_person(session: AsyncSession, ec: ErpCustomer, account_id: uuid.UUID,
                         actor: str, note: str | None = None) -> dict[str, Any]:
    previous = ec.account_id
    result = await link(session, ec.id, account_id, method="manual", actor=actor,
                        reasons=[note] if note else ["Linked by a person"], ctx=None)
    await _manual(session, ec, "erp.customer.linked", actor, {
        "account_id": str(account_id), "previous_account_id": str(previous) if previous else None,
        "note": note, "fields_applied": result["applied"], "fields_for_review": result["review"]})
    return result


async def new_account_by_person(session: AsyncSession, ec: ErpCustomer, actor: str
                                ) -> dict[str, Any]:
    result = await new_account(session, ec.id, actor=actor, ctx=None)
    await _manual(session, ec, "erp.customer.account_created", actor,
                  {"account_id": result["account_id"]})
    return result


async def ignore_by_person(session: AsyncSession, ec: ErpCustomer, actor: str,
                           note: str | None = None) -> None:
    previous = ec.account_id
    _set_link(ec, None, "ignored", "manual", actor, [note] if note else ["Ignored by a person"])
    conn = await session.get(ErpConnection, ec.connection_id)
    await _manual(session, ec, "erp.customer.ignored", actor, {
        "previous_account_id": str(previous) if previous else None, "note": note})
    await refresh_review_task(session, conn)


async def unlink_by_person(session: AsyncSession, ec: ErpCustomer, actor: str,
                           note: str | None = None) -> None:
    """Undo a link. The customer waits in review, and is never linked automatically
    again: only a person links it."""
    previous = ec.account_id
    p = await plan(session, ec)
    _set_link(ec, None, "review", "unlinked", actor,
              [note] if note else ["Unlinked by a person"])
    ec.candidates = p.candidates
    conn = await session.get(ErpConnection, ec.connection_id)
    await _manual(session, ec, "erp.customer.unlinked", actor, {
        "previous_account_id": str(previous) if previous else None, "note": note})
    await refresh_review_task(session, conn)
