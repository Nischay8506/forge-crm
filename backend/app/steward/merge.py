"""Merge and unmerge (F12.1 merge machinery).

merge: per-field survivorship, references from other tables re-pointed to the
survivor in the same transaction, and a MergeEvent holding everything needed
to undo it. The merged record is kept (status="merged"), never deleted.

Modules that reference accounts or contacts must register the column, e.g.
    register_reference("account", Enquiry, "account_id")
so merges re-point their rows too. tests/test_steward.py checks every FK to
accounts/contacts is registered.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import AppError, NotFound
from app.events.outbox import publish
from app.models import (
    Account,
    AutonomyOverride,
    Consent,
    Contact,
    Enquiry,
    ErpCustomer,
    ErpWrite,
    FieldProvenance,
    InboundEmail,
    KillSwitch,
    LegalHold,
    MatchCandidate,
    MergeEvent,
    Message,
    Quote,
    SandboxAction,
)
from app.steward.candidates import decide, ordered
from app.steward.hygiene import refresh_hygiene
from app.steward.identifiers import normalize_domain
from app.steward.records import (
    ALL_FIELDS,
    MODELS,
    SOURCE_TRUST,
    current_provenance,
    record_provenance,
    refresh_match_fields,
    snapshot,
    to_json,
)

REFERENCES: dict[str, list[tuple[type, str]]] = {
    "account": [
        (Account, "parent_id"), (Contact, "account_id"),
        # Agent controls follow the surviving account, so a stop or a stricter autonomy
        # level set on either record still applies after the merge (undo moves them back).
        (KillSwitch, "account_id"), (AutonomyOverride, "account_id"),
        (SandboxAction, "account_id"),
        # ERP customers linked to the account, and writes made for it (F12.6).
        (ErpCustomer, "account_id"), (ErpWrite, "account_id"),
        # A legal hold keeps protecting the surviving account; messages follow it (F12.4).
        (LegalHold, "account_id"), (Message, "account_id"),
        # Enquiries follow the surviving account and contact (M1).
        (Enquiry, "account_id"),
        # Quotes follow the surviving account too (M3).
        (Quote, "account_id"),
    ],
    # Consent follows the surviving contact, so a request to stop made on either
    # record still holds after the merge (F12.5, L9).
    "contact": [(Message, "contact_id"), (Consent, "contact_id"), (Enquiry, "contact_id"),
                (InboundEmail, "contact_id")],
}
UUID_FIELDS = frozenset({"parent_id", "account_id", "merged_into_id"})
LOCATION_FIELDS = ("address_line1", "address_line2", "city", "state", "pincode")
NOT_MERGED_FIELDS = frozenset({"kind", "domain"})


# Tables keyed by the record itself (its own data, e.g. its name vector). They are not
# re-pointed on merge: the merged record keeps its own row.
OWN_ROWS: set[tuple[str, str]] = {("account_embeddings", "account_id")}


def merge_dedupe_key(candidate_id: uuid.UUID) -> str:
    """Key of the Data Steward's approval request for one candidate pair."""
    return f"steward.merge:{candidate_id}"


def register_reference(record_type: str, model: type, column: str) -> None:
    if (model, column) not in REFERENCES[record_type]:
        REFERENCES[record_type].append((model, column))


def _from_json(field: str, value: Any) -> Any:
    if value is not None and field in UUID_FIELDS:
        return uuid.UUID(value)
    return value


def _is_other_registration(s: Account, m: Account) -> bool:
    from app.steward.identifiers import check_gstin, pan_from_gstin

    return (s.kind == m.kind == "entity" and bool(s.gstin) and bool(m.gstin)
            and s.gstin != m.gstin and check_gstin(s.gstin).valid
            and check_gstin(m.gstin).valid and pan_from_gstin(s.gstin) == pan_from_gstin(m.gstin))


def apply_branch_rule(s: Account, m: Account, choices: dict[str, dict[str, Any]]) -> bool:
    """Same legal entity (PAN), different GST registration: the entity keeps its own
    GSTIN and address; the other registration becomes a branch site under it."""
    if not _is_other_registration(s, m):
        return False
    for f in ("gstin", *LOCATION_FIELDS):
        if f in choices:
            choices[f].update(take="survivor", reason="belongs to the other GST registration, "
                              "kept on the branch site")
    choices["_branch_site"] = {"take": "created", "reason": f"GSTIN {m.gstin} will be kept as "
                               "a branch site under this account"}
    return True


def suggest_survivor(a: Account | Contact, b: Account | Contact, record_type: str):
    """The fuller record survives; on a tie, the older one."""
    def filled(r):
        return sum(getattr(r, f) not in (None, "") for f in ALL_FIELDS[record_type])
    return sorted((a, b), key=lambda r: (-filled(r), r.created_at, str(r.id)))


def choose_fields(survivor, merged, record_type: str, prov_s: dict, prov_m: dict,
                  overrides: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """For each field where the two records differ, decide which value the survivor keeps."""
    overrides = overrides or {}
    choices: dict[str, dict[str, Any]] = {}
    for f in ALL_FIELDS[record_type]:
        if f in NOT_MERGED_FIELDS:
            continue
        vs, vm = getattr(survivor, f), getattr(merged, f)
        if vm in (None, "") or vs == vm:
            continue
        entry = {"survivor_value": to_json(vs), "merged_value": to_json(vm)}
        if f in overrides:
            if overrides[f] not in ("survivor", "merged"):
                raise AppError(f"Choice for {f} must be 'survivor' or 'merged'")
            entry.update(take=overrides[f], reason="chosen by reviewer")
        elif vs in (None, ""):
            entry.update(take="merged", reason="survivor had no value")
        else:
            ps, pm = prov_s.get(f), prov_m.get(f)
            ts = SOURCE_TRUST.get(ps.source, 0) if ps else 0
            tm = SOURCE_TRUST.get(pm.source, 0) if pm else 0
            if tm > ts:
                entry.update(take="merged", reason=f"more trusted source ({pm.source})")
            elif tm == ts and pm and ps and pm.verified_at and (
                    ps.verified_at is None or pm.verified_at > ps.verified_at):
                entry.update(take="merged", reason="verified more recently")
            else:
                entry.update(take="survivor", reason="survivor's value is as or more trusted")
        choices[f] = entry
    return choices


async def _load_active(session: AsyncSession, model, rid: uuid.UUID, label: str):
    rec = await session.get(model, rid, with_for_update=True)
    if rec is None:
        raise NotFound(f"{label} not found")
    return rec


async def merge_records(
    session: AsyncSession, record_type: str, survivor_id: uuid.UUID, merged_id: uuid.UUID, *,
    actor: str, automatic: bool, overrides: dict[str, str] | None = None,
) -> MergeEvent:
    model = MODELS[record_type]
    if survivor_id == merged_id:
        raise AppError("Cannot merge a record into itself")
    s = await _load_active(session, model, survivor_id, "Survivor")
    m = await _load_active(session, model, merged_id, "Record to merge")
    if s.status != "active" or m.status != "active":
        raise AppError("Both records must be active to merge")
    if record_type == "account" and s.kind != m.kind:
        raise AppError(f"Cannot merge a {m.kind} into a {s.kind}")

    prov_s, prov_m = await current_provenance(session, s.id), await current_provenance(
        session, m.id)
    s_before, m_before = snapshot(s, record_type), snapshot(m, record_type)
    choices = choose_fields(s, m, record_type, prov_s, prov_m, overrides)
    branch = apply_branch_rule(s, m, choices) if record_type == "account" else False

    event = MergeEvent(tenant_id=s.tenant_id, record_type=record_type, survivor_id=s.id,
                       merged_id=m.id, survivor_before=s_before, merged_before=m_before,
                       survivor_after={}, field_choices={}, repointed={}, automatic=automatic,
                       decided_by=actor)
    session.add(event)
    await session.flush()

    for f, ch in choices.items():
        if ch["take"] != "merged":
            continue
        prev = prov_s.get(f)
        ch["previous_provenance_id"] = str(prev.id) if prev else None
        setattr(s, f, getattr(m, f))
        pm = prov_m.get(f)
        new = await record_provenance(
            session, s, record_type, f, getattr(m, f), source=pm.source if pm else "import",
            actor=actor, source_ref=f"merge:{event.id}",
            confidence=float(pm.confidence) if pm else 1.0)
        new.verified_at = pm.verified_at if pm else None

    repointed: dict[str, list[str]] = {}
    for ref_model, column in REFERENCES[record_type]:
        col = getattr(ref_model, column)
        stmt = (update(ref_model).where(col == m.id, ref_model.id != s.id)
                .values({column: s.id}).returning(ref_model.id)
                .execution_options(synchronize_session="fetch"))
        rows = [str(r) for r in (await session.scalars(stmt)).all()]
        if rows:
            repointed[f"{ref_model.__tablename__}.{column}"] = rows

    if branch:
        from app.steward.records import create_account

        site = await create_account(session, s.tenant_id, {
            "kind": "site", "site_type": "branch", "parent_id": s.id,
            "name": f"{m.name} ({m.city or m.state or m.gstin[:2]})", "gstin": m.gstin,
            "phone": m.phone, **{f: getattr(m, f) for f in LOCATION_FIELDS}},
            source="merge", actor=actor, strict=False, publish_event=False)
        choices.pop("_branch_site", None)
        choices["_branch_site_id"] = {"take": "created", "site_id": str(site.id),
                                      "reason": "other GST registration kept as a branch"}
    if record_type == "account":
        if s.parent_id == m.id:  # survivor was a child of the record being merged
            choices["parent_id"] = {"survivor_value": to_json(s.parent_id),
                                    "merged_value": to_json(m.parent_id), "take": "merged",
                                    "reason": "survivor was a site of the merged record"}
            s.parent_id = m.parent_id
        s.domain = normalize_domain(s.website) or normalize_domain(s.email)
    refresh_match_fields(s)
    m.status, m.merged_into_id = "merged", s.id

    left, right = ordered(s.id, m.id)
    cand = await session.scalar(select(MatchCandidate).where(
        MatchCandidate.record_type == record_type, MatchCandidate.left_id == left,
        MatchCandidate.right_id == right))
    if cand is not None:
        await decide(cand, "merged", actor)
        event.candidate_id = cand.id
    # Other pairs involving the merged record are re-evaluated against the survivor;
    # approvals waiting on those pairs are closed with them.
    stale = (await session.scalars(
        update(MatchCandidate)
        .where(MatchCandidate.status == "open",
               or_(MatchCandidate.left_id == m.id, MatchCandidate.right_id == m.id))
        .values(status="superseded").returning(MatchCandidate.id)
        .execution_options(synchronize_session="fetch"))).all()
    if stale:
        from app.agents.runtime import supersede

        for cid in stale:
            await supersede(session, merge_dedupe_key(cid), actor,
                            "One of the records was merged into another record")

    event.field_choices = choices
    event.repointed = repointed
    event.survivor_after = snapshot(s, record_type)
    await session.flush()
    await refresh_hygiene(session, s, record_type)
    await chain.append(session, s.tenant_id, "record.merged", actor=actor,
                       record_type=record_type, record_id=s.id,
                       data={"merge_event_id": event.id, "survivor_id": s.id,
                             "merged_id": m.id, "automatic": automatic,
                             "repointed": {k: len(v) for k, v in repointed.items()}})
    await publish(session, s.tenant_id, "record.merged", {
        "record_type": record_type, "survivor_id": str(s.id), "merged_id": str(m.id),
        "merge_event_id": str(event.id), "automatic": automatic})
    await publish(session, s.tenant_id, f"{record_type}.updated",
                  {"record_type": record_type, "id": str(s.id)})
    return event


async def undo_merge(session: AsyncSession, event_id: uuid.UUID, *, actor: str) -> MergeEvent:
    ev = await session.get(MergeEvent, event_id, with_for_update=True)
    if ev is None:
        raise NotFound("Merge not found")
    if ev.undone_at is not None:
        raise AppError("This merge was already undone")
    model = MODELS[ev.record_type]
    if "erased" in set(await session.scalars(select(model.status).where(
            model.id.in_((ev.survivor_id, ev.merged_id))))):
        raise AppError("A contact in this merge had their personal data erased, so the merge "
                       "cannot be undone")
    s = await _load_active(session, model, ev.survivor_id, "Survivor")
    m = await _load_active(session, model, ev.merged_id, "Merged record")
    if s.status != "active":
        raise AppError("The surviving record was merged again later; undo that merge first")
    if m.status != "merged" or m.merged_into_id != s.id:
        raise AppError("The merged record has changed since; it cannot be restored automatically")

    notes: list[str] = []
    for f, ch in ev.field_choices.items():
        if ch.get("take") != "merged":
            continue
        if to_json(getattr(s, f)) == ev.survivor_after.get(f):
            setattr(s, f, _from_json(f, ev.survivor_before.get(f)))
            if ch.get("previous_provenance_id"):
                await session.execute(
                    update(FieldProvenance)
                    .where(FieldProvenance.record_id == s.id, FieldProvenance.field == f,
                           FieldProvenance.status == "current")
                    .values(status="rejected"))
                await session.execute(
                    update(FieldProvenance)
                    .where(FieldProvenance.id == uuid.UUID(ch["previous_provenance_id"]))
                    .values(status="current"))
        else:
            notes.append(f"'{f}' was edited after the merge, so its current value was kept")
    await session.execute(
        update(FieldProvenance)
        .where(FieldProvenance.source_ref == f"merge:{ev.id}",
               FieldProvenance.status == "current")
        .values(status="rejected"))

    m.status, m.merged_into_id = "active", None
    branch = ev.field_choices.get("_branch_site_id")
    if branch:
        site = await session.get(Account, uuid.UUID(branch["site_id"]))
        if site is not None and site.status == "active":
            site.status = "inactive"
            notes.append("The branch site created by the merge was deactivated")
    for key, ids_ in ev.repointed.items():
        table, column = key.split(".", 1)
        ref_model = next(mm for mm, c in REFERENCES[ev.record_type]
                         if mm.__tablename__ == table and c == column)
        col = getattr(ref_model, column)
        rows = (await session.scalars(
            update(ref_model)
            .where(ref_model.id.in_([uuid.UUID(i) for i in ids_]), col == s.id)
            .values({column: m.id}).returning(ref_model.id)
            .execution_options(synchronize_session="fetch"))).all()
        if len(rows) != len(ids_):
            notes.append(f"{len(ids_) - len(rows)} {table} row(s) had been moved since the "
                         "merge and were left where they are")

    if ev.record_type == "account":
        s.domain = normalize_domain(s.website) or normalize_domain(s.email)
    refresh_match_fields(s)
    if ev.candidate_id:
        cand = await session.get(MatchCandidate, ev.candidate_id)
        if cand is not None:
            await decide(cand, "not_duplicate", actor)
    ev.undone_at = datetime.now(UTC)
    ev.undone_by = actor
    ev.undo_notes = notes
    await session.flush()
    await refresh_hygiene(session, s, ev.record_type)
    await refresh_hygiene(session, m, ev.record_type)
    await chain.append(session, s.tenant_id, "merge.undone", actor=actor,
                       record_type=ev.record_type, record_id=m.id,
                       data={"merge_event_id": ev.id, "survivor_id": s.id,
                             "restored_id": m.id, "notes": notes})
    await publish(session, s.tenant_id, "record.unmerged", {
        "record_type": ev.record_type, "survivor_id": str(s.id), "restored_id": str(m.id),
        "merge_event_id": str(ev.id)})
    return ev
