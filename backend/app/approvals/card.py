"""What a card shows, and its fingerprint (decisions H6 and H7, safeguards 5 and 6).

Every card follows one contract: the ask, the numbers, the history, and the agent's
recommendation with its reason. History comes from what Forge holds: the account's
tier, earlier decisions on the account and how they ended, and for accounts linked
to an ERP the outstanding, overdue and last-12-months invoiced amounts from Forge's
copy, each with the time it was last synced. Unknown shows as not known; a copy
older than the lag-alert limit is marked as possibly out of date, never as current.

The fingerprint covers everything on the card that a decision rests on. The
browser sends it back with the decision; if the card changed in between, the
decision is refused (safeguard 6). The exact sync time is shown but not
fingerprinted, so a sync that changes no figure does not refuse a decision; a copy
going stale (or fresh) does change the fingerprint.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.controls import account_tiers
from app.agents.policy import display_value
from app.agents.registry import FACTS, TOOLS
from app.audit.chain import canonical
from app.config import get_settings
from app.core.format import inr
from app.models import (
    Account,
    Contact,
    Decision,
    ErpConnection,
    ErpCustomer,
    ErpInvoice,
    SyncFlow,
)

OPEN_INVOICE = ("draft", "cancelled", "paid")


def money(v: Decimal, currency: str) -> str:
    return inr(v) if currency == "INR" else f"{currency} {v:,.2f}"


async def account_of(session: AsyncSession, d: Decision) -> uuid.UUID | None:
    """The account a card is about, if any."""
    if d.record_type == "account" and d.record_id:
        return d.record_id
    if d.record_type == "contact" and d.record_id:
        c = await session.get(Contact, d.record_id)
        if c is not None:
            return c.account_id
    if d.record_type == "enquiry" and d.record_id:
        from app.models import Enquiry

        return await session.scalar(select(Enquiry.account_id).where(Enquiry.id == d.record_id))
    raw = d.args.get("account_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def erp_figures(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """Outstanding, overdue and last-12-months invoiced, per currency, from Forge's
    copy of every ERP the account is linked to, with when that copy was last read."""
    got = await erp_totals(session, account_id)
    if not got["linked"]:
        return got
    return {
        "linked": True, "erps": got["erps"], "as_of": got["as_of"], "stale": got["stale"],
        "figures": [{"currency": cur, "outstanding": money(o, cur), "overdue": money(od, cur),
                     "invoiced_12m": money(inv, cur)} for cur, o, od, inv in got["rows"]],
    }


async def erp_totals(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """The same as erp_figures, as numbers: rows of (currency, outstanding, overdue,
    invoiced in the last 12 months)."""
    links = list(await session.scalars(select(ErpCustomer).where(
        ErpCustomer.account_id == account_id, ErpCustomer.link_status == "linked",
        ErpCustomer.gone_at.is_(None))))
    if not links:
        return {"linked": False}
    conns = {lk.connection_id for lk in links}
    names = dict((await session.execute(select(ErpConnection.id, ErpConnection.name)
                                        .where(ErpConnection.id.in_(conns)))).all())
    synced = dict((await session.execute(select(SyncFlow.connection_id, SyncFlow.last_success_at)
                                         .where(SyncFlow.connection_id.in_(conns),
                                                SyncFlow.entity == "invoice"))).all())
    times = [synced.get(c) for c in conns]
    as_of = None if any(t is None for t in times) else min(times)
    limit = timedelta(minutes=get_settings().integration_lag_alert_minutes)
    stale = as_of is None or datetime.now(UTC) - as_of > limit
    of = or_(*[(ErpInvoice.connection_id == lk.connection_id)
               & (ErpInvoice.customer_external_id == lk.external_id) for lk in links])
    today = date.today()
    live = (ErpInvoice.gone_at.is_(None), ErpInvoice.status.not_in(("draft", "cancelled")))
    rows = (await session.execute(
        select(ErpInvoice.currency,
               func.coalesce(func.sum(ErpInvoice.outstanding).filter(
                   ErpInvoice.status != "paid", ErpInvoice.outstanding > 0), 0),
               func.coalesce(func.sum(ErpInvoice.outstanding).filter(
                   ErpInvoice.status != "paid", ErpInvoice.outstanding > 0,
                   ErpInvoice.due_date < today), 0),
               func.coalesce(func.sum(ErpInvoice.grand_total).filter(
                   ErpInvoice.invoice_date >= today - timedelta(days=365)), 0))
        .where(of, *live).group_by(ErpInvoice.currency).order_by(ErpInvoice.currency))).all()
    return {"linked": True, "erps": sorted(names.get(c, "ERP") for c in conns),
            "as_of": as_of, "stale": stale, "rows": [tuple(r) for r in rows]}


async def history(session: AsyncSession, d: Decision) -> dict[str, Any]:
    acc_id = await account_of(session, d)
    acc = await session.get(Account, acc_id) if acc_id else None
    if acc is None:
        return {"account": None}
    tier = (await account_tiers(session, [acc.id]))[acc.id]
    since = datetime.now(UTC) - timedelta(days=365)
    earlier = list(await session.scalars(select(Decision).where(
        Decision.kind == "approval", Decision.id != d.id, Decision.record_type == "account",
        Decision.record_id == acc.id, Decision.status != "pending",
        Decision.created_at >= since).order_by(Decision.decided_at.desc().nulls_last())))
    tally: dict[str, int] = {}
    for e in earlier:
        tally[e.status] = tally.get(e.status, 0) + 1
    return {
        "account": {"id": str(acc.id), "name": acc.name}, "tier": tier,
        "earlier": {"count": len(earlier), "by_status": tally,
                    "latest": [{"title": e.title, "status": e.status} for e in earlier[:3]]},
        "erp": await erp_figures(session, acc.id),
    }


def _facts(facts: dict[str, Any]) -> list[dict[str, str]]:
    return [{"key": k, "label": FACTS[k].label, "value": display_value(k, v)}
            for k, v in facts.items() if k in FACTS]


async def counter_info(session: AsyncSession, d: Decision) -> dict[str, Any] | None:
    """What was asked and what the approver countered with (H2)."""
    if d.counter_of is None:
        return None
    orig = await session.get(Decision, d.counter_of)
    if orig is None:
        return None
    tool = TOOLS.get(d.tool_key)
    fields = [f for f in (tool.counter_fields if tool else ()) if
              orig.args.get(f) != d.args.get(f)]
    by = next((s for s in orig.steps if s.status == "countered"), None)
    changed = [f for f in orig.facts if orig.facts.get(f) != d.facts.get(f)]
    return {"decision_id": str(orig.id), "fields": fields,
            "asked": [x for x in _facts(orig.facts) if x["key"] in changed],
            "countered": [x for x in _facts(d.facts) if x["key"] in changed],
            "by": by.decided_by if by else None, "on_behalf_of": by.on_behalf_of if by else None,
            "note": by.note if by else None}


async def build(session: AsyncSession, d: Decision, role_names: dict[uuid.UUID, str]
                ) -> dict[str, Any]:
    """The card's decision-relevant content (fingerprinted) plus what is shown but not
    fingerprinted (sync times). Returns {"content", "hash", "history", "counter"}."""
    hist = await history(session, d)
    counter = await counter_info(session, d)
    step = d.current_step
    erp = hist.get("erp") or {}
    content = {
        "decision": str(d.id), "tool": d.tool_key, "title": d.title,
        "facts": [[f["label"], f["value"]] for f in _facts(d.facts)],
        "reasons": d.reasons,
        "recommendation": {"option": d.recommended_option, "why": d.recommendation},
        "action": d.args,
        "step": {"position": d.step_index + 1, "of": len(d.steps),
                 "role": role_names.get(step.role_id) if step and step.role_id else None},
        "approved": [{"step": s.position + 1, "by": s.decided_by, "for": s.on_behalf_of}
                     for s in d.steps if s.status == "approved"],
        "counter": counter,
        "history": {**hist, "erp": {k: v for k, v in erp.items() if k != "as_of"}}
        if hist.get("account") else hist,
    }
    return {"content": content, "hash": fingerprint(content), "history": hist,
            "counter": counter}


def fingerprint(content: dict[str, Any]) -> str:
    import json

    plain = json.loads(json.dumps(content, default=str))
    return hashlib.sha256(canonical(plain).encode()).hexdigest()
