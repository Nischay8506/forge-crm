"""Accounts (company groups, legal entities, sites) and contacts."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permission
from app.core.errors import NotFound
from app.db import get_db
from app.models import Account, Contact, FieldProvenance, MatchCandidate, MergeEvent
from app.steward import records
from app.steward.candidates import account_rec, neighbours_for_account
from app.steward.matching import DISTINCT, AccountRec, compare_accounts
from app.steward.normalize import phonetic_keys

router = APIRouter(tags=["accounts"])


class AccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str | None = None
    site_type: str | None = None
    parent_id: uuid.UUID | None = None
    name: str | None = None
    legal_name: str | None = None
    gstin: str | None = None
    pan: str | None = None
    cin: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    industry: str | None = None
    tier: str | None = None


class AccountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    site_type: str | None
    parent_id: uuid.UUID | None
    name: str
    gstin: str | None
    city: str | None
    state: str | None
    status: str
    merged_into_id: uuid.UUID | None
    hygiene_score: int | None
    tier: str | None


class AccountOut(AccountSummary):
    legal_name: str | None
    pan: str | None
    cin: str | None
    website: str | None
    domain: str | None
    email: str | None
    phone: str | None
    address_line1: str | None
    address_line2: str | None
    pincode: str | None
    country: str
    industry: str | None
    source: str
    owner_id: uuid.UUID | None = None  # whose enquiries these are first (N12 A)
    hygiene_details: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ProvenanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    field: str
    value: str | None
    source: str
    source_ref: str | None
    confidence: float
    status: str
    verified_at: datetime | None
    recorded_by: str | None
    created_at: datetime


class ContactSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID | None
    full_name: str
    email: str | None
    phone: str | None
    designation: str | None
    status: str
    merged_into_id: uuid.UUID | None
    hygiene_score: int | None


class MergeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    record_type: str
    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    automatic: bool
    decided_by: str
    created_at: datetime
    undone_at: datetime | None


class AccountDetail(BaseModel):
    account: AccountOut
    provenance: list[ProvenanceOut]
    proposals: list[ProvenanceOut]
    children: list[AccountSummary]
    contacts: list[ContactSummary]
    merges: list[MergeSummary]
    open_candidates: int


class DuplicateCheck(BaseModel):
    problems: list[str]
    warnings: list[str]
    matches: list[dict[str, Any]]


def _actor(u: CurrentUser) -> str:
    return f"user:{u.user.id}"


async def _get_account(db: AsyncSession, account_id: uuid.UUID) -> Account:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise NotFound("Account not found")
    return acc


@router.get("/accounts", response_model=list[AccountSummary])
async def list_accounts(
    q: str | None = None, kind: str | None = None, status: str = "active",
    parent_id: uuid.UUID | None = None, limit: int = Query(50, le=500), offset: int = 0,
    _: CurrentUser = Depends(require_permission("accounts.read")),
    db: AsyncSession = Depends(get_db),
) -> list[Account]:
    stmt = select(Account).where(Account.status == status)
    if kind:
        stmt = stmt.where(Account.kind == kind)
    if parent_id:
        stmt = stmt.where(Account.parent_id == parent_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Account.name.ilike(like), Account.gstin.ilike(like),
                              Account.city.ilike(like), Account.legal_name.ilike(like)))
    return list(await db.scalars(stmt.order_by(Account.name).limit(limit).offset(offset)))


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(
    body: AccountIn, current: CurrentUser = Depends(require_permission("accounts.write")),
    db: AsyncSession = Depends(get_db),
) -> Account:
    return await records.create_account(db, current.tenant_id, body.model_dump(exclude_unset=True),
                                        source="manual", actor=_actor(current))


@router.post("/accounts/check", response_model=DuplicateCheck)
async def check_account(
    body: AccountIn, _: CurrentUser = Depends(require_permission("accounts.read")),
    db: AsyncSession = Depends(get_db),
) -> DuplicateCheck:
    """Validate a draft and list likely duplicates before it is saved."""
    values, problems, warnings = records.clean_account(body.model_dump(exclude_unset=True))
    draft = AccountRec(id=None, kind=values.get("kind") or "entity", name=values.get("name") or "",
                       gstin=values.get("gstin"), pan=values.get("pan"), cin=values.get("cin"),
                       domain=values.get("domain"), phone=values.get("phone"),
                       pincode=values.get("pincode"), city=values.get("city"),
                       address=" ".join(v for v in (values.get("address_line1"),
                                                    values.get("city")) if v))
    matches = []
    if draft.name:
        for other in await neighbours_for_account(db, draft, phonetic_keys(draft.name), None):
            res = compare_accounts(draft, account_rec(other))
            if res.band != DISTINCT:
                matches.append({"id": str(other.id), "name": other.name, "gstin": other.gstin,
                                "city": other.city, "band": res.band, "score": res.score,
                                "reasons": res.reasons})
    matches.sort(key=lambda m: -m["score"])
    return DuplicateCheck(problems=problems, warnings=warnings, matches=matches[:10])


@router.get("/accounts/{account_id}", response_model=AccountDetail)
async def get_account(
    account_id: uuid.UUID, _: CurrentUser = Depends(require_permission("accounts.read")),
    db: AsyncSession = Depends(get_db),
) -> AccountDetail:
    acc = await _get_account(db, account_id)
    prov = list(await db.scalars(
        select(FieldProvenance).where(FieldProvenance.record_id == acc.id,
                                      FieldProvenance.status.in_(("current", "proposed")))
        .order_by(FieldProvenance.field)))
    children = await db.scalars(select(Account).where(
        Account.parent_id == acc.id, Account.status == "active").order_by(Account.name))
    contacts = await db.scalars(select(Contact).where(
        Contact.account_id == acc.id, Contact.status == "active").order_by(Contact.full_name))
    merges = await db.scalars(select(MergeEvent).where(
        or_(MergeEvent.survivor_id == acc.id, MergeEvent.merged_id == acc.id))
        .order_by(MergeEvent.created_at.desc()))
    open_cands = len((await db.scalars(select(MatchCandidate.id).where(
        MatchCandidate.status == "open",
        or_(MatchCandidate.left_id == acc.id, MatchCandidate.right_id == acc.id)))).all())
    return AccountDetail(
        account=AccountOut.model_validate(acc),
        provenance=[ProvenanceOut.model_validate(p) for p in prov if p.status == "current"],
        proposals=[ProvenanceOut.model_validate(p) for p in prov if p.status == "proposed"],
        children=[AccountSummary.model_validate(c) for c in children],
        contacts=[ContactSummary.model_validate(c) for c in contacts],
        merges=[MergeSummary.model_validate(m) for m in merges],
        open_candidates=open_cands,
    )


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: uuid.UUID, body: AccountIn,
    current: CurrentUser = Depends(require_permission("accounts.write")),
    db: AsyncSession = Depends(get_db),
) -> Account:
    acc = await _get_account(db, account_id)
    return await records.update_account(db, acc, body.model_dump(exclude_unset=True),
                                         source="manual", actor=_actor(current))


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: uuid.UUID | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    designation: str | None = None
    department: str | None = None
    preferred_language: str | None = None


class ContactOut(ContactSummary):
    department: str | None
    preferred_language: str | None
    source: str
    hygiene_details: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ContactRow(ContactSummary):
    account_name: str | None = None


@router.get("/contacts", response_model=list[ContactRow])
async def list_contacts(
    q: str | None = None, account_id: uuid.UUID | None = None, status: str = "active",
    limit: int = Query(50, le=500), offset: int = 0,
    _: CurrentUser = Depends(require_permission("accounts.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ContactRow]:
    stmt = select(Contact, Account.name).outerjoin(Account, Account.id == Contact.account_id) \
        .where(Contact.status == status)
    if account_id:
        stmt = stmt.where(Contact.account_id == account_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Contact.full_name.ilike(like), Contact.email.ilike(like)))
    rows = await db.execute(stmt.order_by(Contact.full_name).limit(limit).offset(offset))
    return [ContactRow(**ContactSummary.model_validate(c).model_dump(), account_name=name)
            for c, name in rows.all()]


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(
    body: ContactIn, current: CurrentUser = Depends(require_permission("accounts.write")),
    db: AsyncSession = Depends(get_db),
) -> Contact:
    return await records.create_contact(db, current.tenant_id, body.model_dump(exclude_unset=True),
                                        source="manual", actor=_actor(current))


@router.get("/contacts/{contact_id}", response_model=ContactOut)
async def get_contact(
    contact_id: uuid.UUID, _: CurrentUser = Depends(require_permission("accounts.read")),
    db: AsyncSession = Depends(get_db),
) -> Contact:
    c = await db.get(Contact, contact_id)
    if c is None:
        raise NotFound("Contact not found")
    return c


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID, body: ContactIn,
    current: CurrentUser = Depends(require_permission("accounts.write")),
    db: AsyncSession = Depends(get_db),
) -> Contact:
    c = await db.get(Contact, contact_id)
    if c is None:
        raise NotFound("Contact not found")
    return await records.update_contact(db, c, body.model_dump(exclude_unset=True),
                                        source="manual", actor=_actor(current))
