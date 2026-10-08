"""Migration mode: bulk-load legacy accounts or contacts from CSV.

Rows are created first (non-strict: bad identifiers are kept and flagged in
hygiene rather than rejected), then the steward runs over the whole batch so
rows can match each other and existing records. Confident duplicates are
merged automatically; the rest land in the review queue (the adjudication
workbench).
"""

import csv
import io
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import AppError
from app.models import Account, ImportBatch, MatchCandidate
from app.steward.embeddings import ensure_embeddings
from app.steward.normalize import normalize_name
from app.steward.records import ValidationFailed, create_account, create_contact
from app.steward.service import process_record

ACCOUNT_COLUMNS = {
    "name": ("name", "company", "company name", "customer", "customer name", "party name"),
    "legal_name": ("legal name", "registered name"),
    "gstin": ("gstin", "gst", "gst no", "gst number", "gstin/uin"),
    "pan": ("pan", "pan no"),
    "cin": ("cin",),
    "website": ("website", "web", "url"),
    "email": ("email", "e-mail", "email id"),
    "phone": ("phone", "mobile", "telephone", "contact no", "phone no"),
    "address_line1": ("address", "address1", "address line 1", "address_line1"),
    "address_line2": ("address2", "address line 2", "address_line2"),
    "city": ("city", "town"),
    "state": ("state",),
    "pincode": ("pincode", "pin", "pin code", "zip", "postal code"),
    "industry": ("industry", "segment"),
    "kind": ("kind", "type"),
    "site_type": ("site type", "site_type"),
}
CONTACT_COLUMNS = {
    "full_name": ("name", "full name", "contact name", "contact person"),
    "email": ("email", "e-mail", "email id"),
    "phone": ("phone", "mobile", "contact no", "phone no"),
    "designation": ("designation", "title", "job title"),
    "department": ("department", "dept"),
    "company": ("company", "company name", "account", "customer"),
    "company_gstin": ("company gstin", "gstin"),
}


def parse_csv(content: bytes, record_type: str) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError("The file has no header row")
    mapping = ACCOUNT_COLUMNS if record_type == "account" else CONTACT_COLUMNS
    header = {h: h.strip().lower() for h in reader.fieldnames}
    colmap = {}
    for target, aliases in mapping.items():
        for raw, low in header.items():
            if low in aliases and raw not in colmap:
                colmap[raw] = target
                break
    name_key = "name" if record_type == "account" else "full_name"
    if name_key not in colmap.values():
        raise AppError("Could not find a name column (expected e.g. 'Name' or 'Company Name')")
    rows = [{colmap[k]: v for k, v in row.items() if k in colmap} for row in reader]
    if len(rows) > get_settings().import_max_rows:
        raise AppError(f"Too many rows ({len(rows)}); split the file into parts of "
                       f"{get_settings().import_max_rows}")
    return rows


async def _link_company(session: AsyncSession, row: dict[str, Any]) -> Any:
    """Find the contact's account by GSTIN, else by exact normalised name (if unique)."""
    gstin = (row.pop("company_gstin", None) or "").strip().upper()
    company = (row.pop("company", None) or "").strip()
    q = None
    if gstin:
        q = select(Account.id).where(Account.status == "active", Account.gstin == gstin)
    elif company:
        q = select(Account.id).where(Account.status == "active",
                                     Account.normalized_name == normalize_name(company))
    if q is None:
        return None
    ids = (await session.scalars(q.limit(2))).all()
    return ids[0] if len(ids) == 1 else None


async def run_import(session: AsyncSession, batch: ImportBatch) -> ImportBatch:
    created = []
    errors: list[dict[str, Any]] = []
    for i, raw in enumerate(batch.rows):
        row = dict(raw)
        try:
            if batch.record_type == "account":
                rec = await create_account(session, batch.tenant_id, row, source="import",
                                           actor=batch.created_by, strict=False,
                                           publish_event=False)
            else:
                row["account_id"] = await _link_company(session, row)
                rec = await create_contact(session, batch.tenant_id, row, source="import",
                                           actor=batch.created_by, strict=False,
                                           publish_event=False)
        except ValidationFailed as exc:
            errors.append({"row": i + 2, "problems": exc.problems})
            continue
        created.append(rec.id)
        warnings = (rec.hygiene_details or {}).get("validity", {})
        if warnings.get("problems"):
            errors.append({"row": i + 2, "problems": warnings["problems"], "kept": True})

    if batch.record_type == "account" and created:
        # One embedding call per 100 rows instead of one per row.
        await ensure_embeddings(session, list(await session.scalars(
            select(Account).where(Account.id.in_(created)))))

    auto = 0
    for rid in created:
        auto += (await process_record(session, batch.record_type, rid))["auto_merged"]

    review = await session.scalar(
        select(func.count()).select_from(MatchCandidate).where(
            MatchCandidate.status == "open",
            or_(MatchCandidate.left_id.in_(created), MatchCandidate.right_id.in_(created))))
    batch.total_rows = len(batch.rows)
    batch.created_count = len(created)
    batch.auto_merged_count = auto
    batch.review_count = review or 0
    batch.error_count = sum(1 for e in errors if not e.get("kept"))
    batch.errors = errors
    batch.status = "done"
    batch.finished_at = datetime.now(UTC)
    return batch
