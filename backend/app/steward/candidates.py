"""Finding duplicate candidates in the database.

Blocking: only records sharing at least one key (PAN, CIN, GSTIN, domain, phone,
pincode or a phonetic name code) are compared, so the cost stays proportional to
the number of plausible neighbours, not the size of the table. When embeddings are
on, the nearest accounts by meaning are added to that set (see embeddings.py).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Account, Contact, MatchCandidate
from app.steward.embeddings import meaning_neighbours
from app.steward.matching import (
    DISTINCT,
    EMBEDDING_REVIEW,
    AccountRec,
    ContactRec,
    MatchResult,
    compare_accounts,
    compare_contacts,
)


@dataclass
class Found:
    other_id: uuid.UUID
    result: MatchResult


def account_rec(a: Account) -> AccountRec:
    address = " ".join(x for x in (a.address_line1, a.address_line2, a.city) if x)
    return AccountRec(id=a.id, kind=a.kind, name=a.name, gstin=a.gstin, pan=a.pan, cin=a.cin,
                      domain=a.domain, phone=a.phone, pincode=a.pincode, city=a.city,
                      address=address, parent_id=a.parent_id)


def contact_rec(c: Contact) -> ContactRec:
    return ContactRec(id=c.id, full_name=c.full_name, email=c.email, phone=c.phone,
                      account_id=c.account_id)


async def neighbours_for_account(session: AsyncSession, a: Account | AccountRec,
                                 phonetic: list[str], exclude_id: uuid.UUID | None):
    rec = a if isinstance(a, AccountRec) else account_rec(a)
    keys = []
    if rec.effective_pan:
        keys += [Account.pan == rec.effective_pan]
    for col, val in ((Account.cin, rec.cin), (Account.gstin, rec.gstin),
                     (Account.domain, rec.domain), (Account.phone, rec.phone),
                     (Account.pincode, rec.pincode)):
        if val:
            keys.append(col == val)
    if phonetic:
        keys.append(Account.phonetic_keys.overlap(phonetic))
    if not keys:
        return []
    q = select(Account).where(Account.status == "active", Account.kind == rec.kind, or_(*keys))
    if exclude_id is not None:
        q = q.where(Account.id != exclude_id)
    return list(await session.scalars(q.limit(get_settings().steward_candidate_limit)))


async def find_account_matches(session: AsyncSession, a: Account) -> list[Found]:
    rec = account_rec(a)
    sims = await meaning_neighbours(session, a)
    others = {o.id: o for o in await neighbours_for_account(session, a, a.phonetic_keys or [],
                                                            a.id)}
    # Pairs only the meaning match found (no shared key) are compared too: review at most.
    extra = [i for i, sim in sims.items() if sim >= EMBEDDING_REVIEW and i not in others]
    if extra:
        others.update({o.id: o for o in await session.scalars(
            select(Account).where(Account.id.in_(extra)))})
    found = []
    for other in others.values():
        res = compare_accounts(rec, account_rec(other), embedding_similarity=sims.get(other.id),
                               name_only_auto_merge=get_settings().steward_name_only_auto_merge)
        if res.band != DISTINCT:
            found.append(Found(other.id, res))
    return found


async def find_contact_matches(session: AsyncSession, c: Contact) -> list[Found]:
    keys = []
    if c.email:
        keys.append(Contact.email == c.email)
    if c.phone:
        keys.append(Contact.phone == c.phone)
    if c.phonetic_keys:
        keys.append(Contact.phonetic_keys.overlap(c.phonetic_keys))
    if not keys:
        return []
    others = await session.scalars(
        select(Contact).where(Contact.status == "active", Contact.id != c.id, or_(*keys))
        .limit(get_settings().steward_candidate_limit))
    found = []
    rec = contact_rec(c)
    for other in others:
        res = compare_contacts(rec, contact_rec(other))
        if res.band != DISTINCT:
            found.append(Found(other.id, res))
    return found


def ordered(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if str(a) < str(b) else (b, a)


async def upsert_candidate(session: AsyncSession, tenant_id: uuid.UUID, record_type: str,
                           record_id: uuid.UUID, found: Found) -> MatchCandidate | None:
    """Store or refresh a candidate. A pair a person already decided is left alone,
    so 'not a duplicate' is never asked again."""
    left, right = ordered(record_id, found.other_id)
    cand = await session.scalar(select(MatchCandidate).where(
        MatchCandidate.record_type == record_type, MatchCandidate.left_id == left,
        MatchCandidate.right_id == right))
    if cand is None:
        cand = MatchCandidate(tenant_id=tenant_id, record_type=record_type, left_id=left,
                              right_id=right, status="open")
        session.add(cand)
    elif cand.status in ("not_duplicate", "merged"):
        return None
    elif cand.status == "superseded":
        cand.status = "open"
        cand.decided_at = None
        cand.decided_by = None
    cand.score = Decimal(str(found.result.score))
    cand.band = found.result.band
    cand.reasons = found.result.reasons
    await session.flush()
    return cand


async def decide(cand: MatchCandidate, status: str, actor: str) -> None:
    cand.status = status
    cand.decided_by = actor
    cand.decided_at = datetime.now(UTC)
