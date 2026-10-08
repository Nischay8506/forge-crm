"""The Data Steward's main loop for one record: find candidates, auto-merge the
confident ones, queue the rest for a person, keep hygiene scores current.

Runs from outbox handlers (account/contact created or updated) and from imports.
From Step 2 (agent runtime) the auto-merge call goes through the policy gate.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.steward.candidates import find_account_matches, find_contact_matches, upsert_candidate
from app.steward.hygiene import refresh_hygiene
from app.steward.matching import AUTO_MERGE
from app.steward.merge import merge_records, suggest_survivor
from app.steward.records import MODELS

STEWARD_ACTOR = "agent:data_steward"


async def process_record(session: AsyncSession, record_type: str,
                         record_id: uuid.UUID) -> dict[str, Any]:
    model = MODELS[record_type]
    rec = await session.get(model, record_id)
    summary = {"auto_merged": 0, "queued": 0}
    if rec is None or rec.status != "active":
        return summary
    finder = find_account_matches if record_type == "account" else find_contact_matches
    found = sorted(await finder(session, rec),
                   key=lambda f: (f.result.band != AUTO_MERGE, -f.result.score))
    touched: set[uuid.UUID] = set()
    for f in found:
        cand = await upsert_candidate(session, rec.tenant_id, record_type, rec.id, f)
        if cand is None:
            continue
        other = await session.get(model, f.other_id)
        if f.result.band == AUTO_MERGE and get_settings().steward_auto_merge:
            if other is None or other.status != "active" or rec.status != "active":
                continue
            survivor, loser = suggest_survivor(rec, other, record_type)
            await merge_records(session, record_type, survivor.id, loser.id,
                                actor=STEWARD_ACTOR, automatic=True)
            summary["auto_merged"] += 1
            if rec.status != "active":
                break  # this record was absorbed; the survivor is re-checked via its event
        else:
            summary["queued"] += 1
            if other is not None:
                touched.add(other.id)
    if rec.status == "active":
        await refresh_hygiene(session, rec, record_type)
    for oid in touched:
        other = await session.get(model, oid)
        if other is not None and other.status == "active":
            await refresh_hygiene(session, other, record_type)
    return summary
