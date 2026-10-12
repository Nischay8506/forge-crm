"""Who owns an enquiry (F1.9, decision N12 A).

The company's steps are tried in order: the account's owner, then someone who looks
after the product family, then someone who looks after the customer's state or
city, then the next person in the team by fewest open enquiries (the load cap
applies to the team steps, not to an account's own owner). Someone who is "Away"
(Forge's Away, Step 5) is passed over; for an account owner, their stand-in takes it.
With nobody found, the fallback person gets it, else it waits unassigned and the
company's escalation role (else full-access admins) is told.

A person can give it to someone else, with a reason. When people change the owner a
rule chose three times in 30 days, the Routing screen suggests looking at that rule.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.enquiries import records
from app.enquiries.settings import STEP_LABELS
from app.models import Account, Delegation, Enquiry, EnquiryAssignment, User
from app.models.enquiries import OPEN_STATUSES

OVERRIDES_TO_SUGGEST = 3
OVERRIDE_DAYS = 30


async def away(session: AsyncSession, now: datetime) -> dict[uuid.UUID, uuid.UUID]:
    """People who are away now, and who stands in for each."""
    rows = await session.execute(select(Delegation.from_user_id, Delegation.to_user_id).where(
        Delegation.kind == "away", Delegation.revoked_at.is_(None),
        Delegation.starts_at <= now,
        (Delegation.ends_at.is_(None)) | (Delegation.ends_at > now)))
    return {f: t for f, t in rows.all()}


async def open_counts(session: AsyncSession) -> dict[uuid.UUID, int]:
    rows = await session.execute(select(Enquiry.owner_id, func.count()).where(
        Enquiry.owner_id.is_not(None), Enquiry.status.in_(OPEN_STATUSES))
        .group_by(Enquiry.owner_id))
    return dict(rows.all())


async def _last_given(session: AsyncSession) -> dict[uuid.UUID, datetime]:
    rows = await session.execute(select(EnquiryAssignment.user_id,
                                        func.max(EnquiryAssignment.at))
                                 .where(EnquiryAssignment.user_id.is_not(None))
                                 .group_by(EnquiryAssignment.user_id))
    return dict(rows.all())


async def account_owner(session: AsyncSession, account_id: uuid.UUID | None
                        ) -> uuid.UUID | None:
    """The account's owner, else its company's, else its group's."""
    for _ in range(4):
        if account_id is None:
            return None
        row = (await session.execute(select(Account.owner_id, Account.parent_id)
                                     .where(Account.id == account_id))).first()
        if row is None:
            return None
        if row.owner_id:
            return row.owner_id
        account_id = row.parent_id
    return None


async def _place(session: AsyncSession, enq: Enquiry) -> set[str]:
    words = {str(enq.customer.get(k) or "").strip().lower() for k in ("state", "city")}
    if enq.account_id:
        acc = await session.get(Account, enq.account_id)
        if acc:
            words |= {(acc.state or "").lower(), (acc.city or "").lower()}
    return {w for w in words if w}


def _pick(pool: list[User], counts: dict[uuid.UUID, int], last: dict[uuid.UUID, datetime],
          cap: int) -> User | None:
    ok = [u for u in pool if counts.get(u.id, 0) < (u.max_open_enquiries or cap)]
    if not ok:
        return None
    oldest = datetime.min.replace(tzinfo=UTC)
    return min(ok, key=lambda u: (counts.get(u.id, 0), last.get(u.id, oldest), u.full_name))


async def choose(session: AsyncSession, enq: Enquiry, cfg: dict[str, Any], now: datetime
                 ) -> tuple[uuid.UUID | None, str, str]:
    """(user, rule, reason in plain words)."""
    gone = await away(session, now)
    team = [u for u in await session.scalars(select(User).where(
        User.is_active.is_(True), User.takes_enquiries.is_(True)))]
    counts, last = await open_counts(session), await _last_given(session)
    cap = int(cfg.get("max_open") or 25)
    present = [u for u in team if u.id not in gone]
    for step in cfg.get("steps") or []:
        if step == "account_owner":
            owner_id = await account_owner(session, enq.account_id)
            owner = await session.get(User, owner_id) if owner_id else None
            if owner is None or not owner.is_active:
                continue
            if owner.id in gone:
                stand_in = await session.get(User, gone[owner.id])
                if stand_in is not None and stand_in.is_active:
                    return stand_in.id, "away", (f"{owner.full_name} owns the account and is "
                                                 f"away; {stand_in.full_name} stands in")
                continue
            return owner.id, step, f"{owner.full_name} owns the account"
        if step == "product_line" and enq.product_family_id:
            pool = [u for u in present if str(enq.product_family_id) in
                    {str(x) for x in u.product_family_ids or []}]
            u = _pick(pool, counts, last, cap)
            if u:
                return u.id, step, f"{u.full_name} looks after this product family"
        if step == "region":
            place = await _place(session, enq)
            pool = [u for u in present if place & {r.strip().lower() for r in u.regions or []}]
            u = _pick(pool, counts, last, cap) if place else None
            if u:
                return u.id, step, f"{u.full_name} looks after {', '.join(sorted(place)).title()}"
        if step == "round_robin":
            u = _pick(present, counts, last, cap)
            if u:
                return u.id, step, (f"{u.full_name} is next in the team "
                                    f"({counts.get(u.id, 0)} open)")
    fb = cfg.get("fallback_user_id")
    if fb:
        u = await session.get(User, uuid.UUID(str(fb)))
        if u is not None and u.is_active:
            return u.id, "fallback", f"Nobody else fitted, so {u.full_name} (the fallback) has it"
    return None, "none", "Nobody fitted the routing steps and there is no fallback person"


async def assign(session: AsyncSession, enq: Enquiry, user_id: uuid.UUID | None, *, rule: str,
                 reason: str, actor: str, overrode_rule: str | None = None,
                 now: datetime | None = None) -> EnquiryAssignment:
    from app.enquiries import timers

    now = now or datetime.now(UTC)
    row = EnquiryAssignment(tenant_id=enq.tenant_id, enquiry_id=enq.id, user_id=user_id,
                            previous_user_id=enq.owner_id, rule=rule,
                            overrode_rule=overrode_rule, reason=reason[:300], actor=actor, at=now)
    session.add(row)
    previous = enq.owner_id
    enq.owner_id, enq.assigned_rule, enq.assigned_at = user_id, rule, now
    enq.owner_seen_at = None
    if enq.status == "new" and user_id:
        enq.status = "open"
    await records.event(session, enq, "assigned", actor, user_id=user_id, rule=rule,
                        reason=reason, previous_user_id=previous, overrode_rule=overrode_rule)
    await records.audit(session, enq, "enquiry.assigned", actor, user_id=user_id, rule=rule,
                        previous_user_id=previous, overrode_rule=overrode_rule)
    await timers.restart_first_look(session, enq, now)
    who = await session.get(User, user_id) if user_id else None
    if who is not None:
        await notify_people(session, enq, {who.id}, f"New enquiry for you: {enq.ref}",
                            enq.summary or "Open it to see what the customer asks for")
    else:
        await notify_people(session, enq, await timers.escalation_people(session, enq),
                            f"{enq.ref} has no owner",
                            "Nobody fitted the routing steps. Give it to someone.")
    return row


async def route(session: AsyncSession, enq: Enquiry, cfg: dict[str, Any], *, actor: str,
                now: datetime | None = None) -> EnquiryAssignment:
    now = now or datetime.now(UTC)
    user_id, rule, reason = await choose(session, enq, cfg, now)
    return await assign(session, enq, user_id, rule=rule, reason=reason, actor=actor, now=now)


async def reassign(session: AsyncSession, enq: Enquiry, user_id: uuid.UUID, *, reason: str,
                   actor: str) -> EnquiryAssignment:
    if enq.status not in OPEN_STATUSES:
        raise AppError(f"{enq.ref} is {enq.status}, so it cannot be given to someone else")
    reason = (reason or "").strip()
    if not reason:
        raise AppError("Say why it goes to someone else")
    u = await session.get(User, user_id)
    if u is None or not u.is_active:
        raise AppError("That person is not an active user")
    if u.id == enq.owner_id:
        raise AppError(f"{u.full_name} already has it")
    overrode = enq.assigned_rule if enq.assigned_rule in STEP_LABELS else None
    return await assign(session, enq, u.id, rule="manual", reason=reason, actor=actor,
                        overrode_rule=overrode)


async def suggestions(session: AsyncSession, now: datetime | None = None
                      ) -> list[dict[str, Any]]:
    """Rules people overrode at least three times in the last 30 days."""
    now = now or datetime.now(UTC)
    rows = await session.execute(
        select(EnquiryAssignment.overrode_rule, func.count()).where(
            EnquiryAssignment.overrode_rule.is_not(None),
            EnquiryAssignment.at >= now - timedelta(days=OVERRIDE_DAYS))
        .group_by(EnquiryAssignment.overrode_rule))
    return [{"rule": rule, "label": STEP_LABELS.get(rule, rule), "overrides": n,
             "text": f"People changed the owner chosen by '{STEP_LABELS.get(rule, rule)}' "
                     f"{n} times in the last {OVERRIDE_DAYS} days. Check who this step "
                     "points to (people's product families and regions)."}
            for rule, n in rows.all() if n >= OVERRIDES_TO_SUGGEST]


async def notify_people(session: AsyncSession, enq: Enquiry, user_ids: set[uuid.UUID],
                        title: str, body: str) -> None:
    from app.approvals.notify import tell

    if user_ids:
        await tell(session, enq.tenant_id, user_ids, title, body,
                   f"/enquiries/{enq.id}", f"enquiry-{enq.id}")
