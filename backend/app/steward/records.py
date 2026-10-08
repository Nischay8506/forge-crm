"""Creating and updating golden records, with validation and field provenance.

Every write goes through here so that identifiers are normalised the same way,
provenance is recorded for each key field, matching helpers stay current and an
outbox event tells the Data Steward to look for duplicates.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ValidationFailed
from app.events.outbox import publish
from app.models import Account, Contact, FieldProvenance
from app.models.golden import ACCOUNT_KINDS, ACCOUNT_TIERS, SITE_TYPES
from app.steward import identifiers as ids
from app.steward.normalize import normalize_name, phonetic_keys

# Higher wins when two sources disagree (used by survivorship and enrichment).
SOURCE_TRUST: dict[str, int] = {
    "registry": 100, "erp": 90, "manual": 80, "document": 60, "import": 50, "merge": 50,
    "signature": 40, "agent": 30,
}
# Sources whose values count as verified at the time they are recorded.
VERIFYING_SOURCES = frozenset({"registry", "erp", "manual"})

ACCOUNT_FIELDS = (
    "kind", "site_type", "parent_id", "name", "legal_name", "gstin", "pan", "cin", "website",
    "domain", "email", "phone", "address_line1", "address_line2", "city", "state", "pincode",
    "country", "industry", "tier",
)
ACCOUNT_KEY_FIELDS = (
    "name", "legal_name", "gstin", "pan", "cin", "website", "email", "phone", "address_line1",
    "city", "state", "pincode", "industry",
)
CONTACT_FIELDS = (
    "account_id", "full_name", "email", "phone", "designation", "department",
    "preferred_language",
)
CONTACT_KEY_FIELDS = ("full_name", "email", "phone", "designation", "department")

MODELS = {"account": Account, "contact": Contact}
KEY_FIELDS = {"account": ACCOUNT_KEY_FIELDS, "contact": CONTACT_KEY_FIELDS}
ALL_FIELDS = {"account": ACCOUNT_FIELDS, "contact": CONTACT_FIELDS}


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


def clean_account(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Normalise account input. Returns (values, errors, warnings).
    Errors block a manual save; on import they are kept as warnings and lower hygiene."""
    out: dict[str, Any] = {k: (None if _blank(v) else _strip(v)) for k, v in data.items()
                           if k in ACCOUNT_FIELDS}
    errors: list[str] = []
    warnings: list[str] = []

    if "name" in out and not out["name"]:
        errors.append("Name is required")
    if out.get("kind") and out["kind"] not in ACCOUNT_KINDS:
        errors.append(f"kind must be one of {', '.join(ACCOUNT_KINDS)}")
    if out.get("site_type") and out["site_type"] not in SITE_TYPES:
        errors.append(f"site_type must be one of {', '.join(SITE_TYPES)}")
    if out.get("tier"):
        out["tier"] = out["tier"].lower()
        if out["tier"] not in ACCOUNT_TIERS:
            errors.append(f"tier must be one of {', '.join(ACCOUNT_TIERS)}")

    if "gstin" in out:
        g = ids.check_gstin(out["gstin"])
        out["gstin"] = g.value
        errors += list(g.problems)
        if g.valid and g.value:
            derived = ids.pan_from_gstin(g.value)
            if _blank(out.get("pan")):
                out["pan"] = derived
            elif ids.check_pan(out["pan"]).value != derived:
                errors.append(f"PAN {out['pan']} does not match the PAN inside GSTIN ({derived})")
            gst_state = ids.gst_state(g.value)
            if _blank(out.get("state")):
                out["state"] = gst_state
            elif gst_state and out["state"].lower() != gst_state.lower():
                warnings.append(f"State '{out['state']}' differs from the GSTIN's state "
                                f"({gst_state})")
    if "pan" in out:
        p = ids.check_pan(out["pan"])
        out["pan"] = p.value
        errors += list(p.problems)
    if "cin" in out:
        c = ids.check_cin(out["cin"])
        out["cin"] = c.value
        errors += list(c.problems)
    if "phone" in out and out["phone"]:
        phone = ids.normalize_phone(out["phone"])
        if phone is None:
            errors.append(f"Phone '{out['phone']}' is not a valid number")
        else:
            out["phone"] = phone
    if "email" in out and out["email"]:
        email = ids.normalize_email(out["email"])
        if email is None:
            errors.append(f"Email '{out['email']}' is not valid")
        else:
            out["email"] = email
    if "pincode" in out and out["pincode"]:
        pin = ids.normalize_pincode(out["pincode"])
        if pin is None:
            errors.append(f"Pincode '{out['pincode']}' is not a valid 6-digit PIN")
        else:
            out["pincode"] = pin
    if "website" in out or "email" in out:
        out["domain"] = ids.normalize_domain(out.get("website")) or ids.normalize_domain(
            out.get("email"))
    return out, errors, warnings


def clean_contact(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    out: dict[str, Any] = {k: (None if _blank(v) else _strip(v)) for k, v in data.items()
                           if k in CONTACT_FIELDS}
    errors: list[str] = []
    if "full_name" in out and not out["full_name"]:
        errors.append("Name is required")
    if "phone" in out and out["phone"]:
        phone = ids.normalize_phone(out["phone"])
        if phone is None:
            errors.append(f"Phone '{out['phone']}' is not a valid number")
        else:
            out["phone"] = phone
    if "email" in out and out["email"]:
        email = ids.normalize_email(out["email"])
        if email is None:
            errors.append(f"Email '{out['email']}' is not valid")
        else:
            out["email"] = email
    return out, errors, []


def refresh_match_fields(record: Account | Contact) -> None:
    name = record.name if isinstance(record, Account) else record.full_name
    record.normalized_name = normalize_name(name)
    record.phonetic_keys = phonetic_keys(name)


def to_json(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def snapshot(record: Account | Contact, record_type: str) -> dict[str, Any]:
    fields = ALL_FIELDS[record_type] + ("status", "merged_into_id")
    return {f: to_json(getattr(record, f)) for f in fields}


async def record_provenance(
    session: AsyncSession, record: Account | Contact, record_type: str, field: str,
    value: Any, *, source: str, actor: str, source_ref: str | None = None,
    confidence: float = 1.0,
) -> FieldProvenance:
    """Make `value` the current provenance for a field (older current rows are superseded)."""
    await session.execute(
        update(FieldProvenance)
        .where(FieldProvenance.record_id == record.id, FieldProvenance.field == field,
               FieldProvenance.status == "current")
        .values(status="superseded")
    )
    prov = FieldProvenance(
        tenant_id=record.tenant_id, record_type=record_type, record_id=record.id, field=field,
        value=None if value is None else str(to_json(value)), source=source,
        source_ref=source_ref, confidence=Decimal(str(confidence)), status="current",
        verified_at=datetime.now(UTC) if source in VERIFYING_SOURCES else None,
        recorded_by=actor,
    )
    session.add(prov)
    return prov


async def current_provenance(
    session: AsyncSession, record_id: uuid.UUID
) -> dict[str, FieldProvenance]:
    rows = await session.scalars(
        select(FieldProvenance).where(FieldProvenance.record_id == record_id,
                                      FieldProvenance.status == "current")
    )
    return {p.field: p for p in rows}


async def _after_write(session: AsyncSession, record: Account | Contact, record_type: str,
                       event: str | None, warnings: list[str]) -> None:
    from app.steward.hygiene import refresh_hygiene

    await session.flush()
    await refresh_hygiene(session, record, record_type, extra_warnings=warnings)
    if event:
        await publish(session, record.tenant_id, event, {"record_type": record_type,
                                                         "id": str(record.id)})


async def create_account(
    session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any], *, source: str,
    actor: str, strict: bool = True, publish_event: bool = True,
) -> Account:
    values, errors, warnings = clean_account(data)
    if not values.get("name"):
        raise ValidationFailed(["Name is required"])
    if strict and errors:
        raise ValidationFailed(errors)
    if values.get("parent_id"):
        await _check_parent(session, values["parent_id"])
    acc = Account(tenant_id=tenant_id, source=source,
                  **{k: v for k, v in values.items() if v is not None})
    refresh_match_fields(acc)
    session.add(acc)
    await session.flush()
    for f in ACCOUNT_KEY_FIELDS:
        if getattr(acc, f) is not None:
            await record_provenance(session, acc, "account", f, getattr(acc, f), source=source,
                                    actor=actor)
    await _after_write(session, acc, "account", "account.created" if publish_event else None,
                       warnings + (errors if not strict else []))
    return acc


async def update_account(
    session: AsyncSession, acc: Account, changes: dict[str, Any], *, source: str, actor: str,
    strict: bool = True,
) -> Account:
    if acc.status != "active":
        raise AppError(f"Account is {acc.status}; edit the surviving record instead")
    values, errors, warnings = clean_account(changes)
    if strict and errors:
        raise ValidationFailed(errors)
    if values.get("parent_id"):
        if str(values["parent_id"]) == str(acc.id):
            raise ValidationFailed(["An account cannot be its own parent"])
        await _check_parent(session, values["parent_id"])
    changed = False
    for f, v in values.items():
        if getattr(acc, f) != v:
            setattr(acc, f, v)
            changed = True
            if f in ACCOUNT_KEY_FIELDS:
                await record_provenance(session, acc, "account", f, v, source=source, actor=actor)
    if changed:
        refresh_match_fields(acc)
    await _after_write(session, acc, "account", "account.updated" if changed else None,
                       warnings + (errors if not strict else []))
    return acc


async def _check_parent(session: AsyncSession, parent_id: Any) -> None:
    parent = await session.get(Account, uuid.UUID(str(parent_id)))
    if parent is None or parent.status != "active":
        raise ValidationFailed(["Parent account not found"])


async def create_contact(
    session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any], *, source: str,
    actor: str, strict: bool = True, publish_event: bool = True,
) -> Contact:
    values, errors, warnings = clean_contact(data)
    if not values.get("full_name"):
        raise ValidationFailed(["Name is required"])
    if strict and errors:
        raise ValidationFailed(errors)
    if values.get("account_id"):
        await _check_parent(session, values["account_id"])
    c = Contact(tenant_id=tenant_id, source=source,
                **{k: v for k, v in values.items() if v is not None})
    refresh_match_fields(c)
    session.add(c)
    await session.flush()
    for f in CONTACT_KEY_FIELDS:
        if getattr(c, f) is not None:
            await record_provenance(session, c, "contact", f, getattr(c, f), source=source,
                                    actor=actor)
    await _after_write(session, c, "contact", "contact.created" if publish_event else None,
                       warnings + (errors if not strict else []))
    return c


async def update_contact(
    session: AsyncSession, c: Contact, changes: dict[str, Any], *, source: str, actor: str,
    strict: bool = True,
) -> Contact:
    if c.status != "active":
        raise AppError(f"Contact is {c.status}; edit the surviving record instead")
    values, errors, warnings = clean_contact(changes)
    if strict and errors:
        raise ValidationFailed(errors)
    if values.get("account_id"):
        await _check_parent(session, values["account_id"])
    changed = False
    for f, v in values.items():
        if getattr(c, f) != v:
            setattr(c, f, v)
            changed = True
            if f in CONTACT_KEY_FIELDS:
                await record_provenance(session, c, "contact", f, v, source=source, actor=actor)
    if changed:
        refresh_match_fields(c)
    await _after_write(session, c, "contact", "contact.updated" if changed else None,
                       warnings + (errors if not strict else []))
    return c
