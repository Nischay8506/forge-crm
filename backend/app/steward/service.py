"""The Data Steward's main loop for one record: find candidates, propose merging
the confident ones, queue the rest for a person, keep hygiene scores current.

Runs from outbox handlers (account/contact created or updated) and from imports.
Each proposed merge goes through the policy gate (app/agents/runtime.py): it
happens, waits for a person's approval, or is blocked, and lands in the audit log.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime
from app.agents.registry import AgentContext, load_definitions
from app.steward.candidates import find_account_matches, find_contact_matches, upsert_candidate
from app.steward.hygiene import refresh_hygiene
from app.steward.matching import AUTO_MERGE, MatchResult
from app.steward.merge import merge_dedupe_key, suggest_survivor
from app.steward.records import MODELS


def _recommendation(record_type: str, result: MatchResult) -> str:
    why = "; ".join(r["detail"] for r in result.reasons
                   if r.get("effect") in ("strong", "medium"))[:600]
    return f"Merge: these look like the same {record_type} (score {result.score:.0f}). {why}"


async def process_record(session: AsyncSession, record_type: str,
                         record_id: uuid.UUID) -> dict[str, Any]:
    model = MODELS[record_type]
    rec = await session.get(model, record_id)
    # queued: pairs left open for a person, including merges waiting for approval,
    # blocked by the gate or failed, which are also counted separately.
    summary = {"auto_merged": 0, "queued": 0, "waiting": 0, "blocked": 0, "failed": 0}
    if rec is None or rec.status != "active":
        return summary
    finder = find_account_matches if record_type == "account" else find_contact_matches
    found = sorted(await finder(session, rec),
                   key=lambda f: (f.result.band != AUTO_MERGE, -f.result.score))
    touched: set[uuid.UUID] = set()
    ctx: AgentContext | None = None
    for f in found:
        cand = await upsert_candidate(session, rec.tenant_id, record_type, rec.id, f)
        if cand is None:
            continue
        other = await session.get(model, f.other_id)
        if f.result.band == AUTO_MERGE:
            if other is None or other.status != "active" or rec.status != "active":
                continue
            survivor, loser = suggest_survivor(rec, other, record_type)
            if ctx is None:
                load_definitions()
                ctx = await runtime.start_run(session, rec.tenant_id, "data_steward",
                                              f"{record_type}.check", str(rec.id))
            out = await runtime.call_tool(
                session, ctx, "steward.merge_records",
                {"record_type": record_type, "survivor_id": survivor.id,
                 "merged_id": loser.id, "candidate_id": cand.id},
                recommendation=_recommendation(record_type, f.result),
                dedupe_key=merge_dedupe_key(cand.id))
            if out.status == "done":
                summary["auto_merged"] += 1
                if rec.status != "active":
                    break  # this record was absorbed; the survivor is re-checked via its event
                continue
            summary[out.status if out.status in ("waiting", "blocked") else "failed"] += 1
        summary["queued"] += 1
        if other is not None:
            touched.add(other.id)
    if rec.status == "active":
        await refresh_hygiene(session, rec, record_type)
    for oid in touched:
        other = await session.get(model, oid)
        if other is not None and other.status == "active":
            await refresh_hygiene(session, other, record_type)
    if ctx is not None:
        await runtime.finish_run(session, ctx, summary=summary)
    return summary
