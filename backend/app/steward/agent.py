"""The Data Steward as an agent (decision T7): its automatic merges go through the
policy gate like any other agent action, so tenants can stop it, lower its level,
or write rules such as "ask a person before merging strategic accounts".

The tool computes its own facts (match score, account tier, how many linked rows
move) from the records, so a rule never depends on what the caller claims.
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.controls import account_tiers
from app.agents.registry import (
    L2,
    L3,
    AgentContext,
    AgentDef,
    ToolDef,
    Touches,
    register_agent,
    register_tool,
)
from app.models import Contact, MatchCandidate
from app.models.golden import ACCOUNT_TIERS
from app.steward.merge import REFERENCES, merge_records
from app.steward.records import MODELS

AGENT_KEY = "data_steward"
MERGE_TOOL = "steward.merge_records"


class MergeArgs(BaseModel):
    record_type: Literal["account", "contact"]
    survivor_id: uuid.UUID
    merged_id: uuid.UUID
    candidate_id: uuid.UUID | None = None


def top_tier(tiers: list[str | None]) -> str:
    """The most important tier among the records (strategic > key > standard)."""
    ranks = [ACCOUNT_TIERS.index(t) if t in ACCOUNT_TIERS else len(ACCOUNT_TIERS) - 1
             for t in tiers] or [len(ACCOUNT_TIERS) - 1]
    return ACCOUNT_TIERS[min(ranks)]


async def _account_ids(session: AsyncSession, a: MergeArgs) -> list[uuid.UUID]:
    if a.record_type == "account":
        return [a.survivor_id, a.merged_id]
    ids = (await session.scalars(select(Contact.account_id).where(
        Contact.id.in_([a.survivor_id, a.merged_id]), Contact.account_id.is_not(None)))).all()
    return list(dict.fromkeys(ids))


async def _facts(session: AsyncSession, a: MergeArgs) -> dict[str, Any]:
    facts: dict[str, Any] = {"record_type": a.record_type}
    if a.candidate_id:
        score = await session.scalar(select(MatchCandidate.score)
                                     .where(MatchCandidate.id == a.candidate_id))
        if score is not None:
            facts["match_score"] = float(score)
    accounts = await _account_ids(session, a)
    facts["account_tier"] = top_tier(list((await account_tiers(session, accounts)).values()))
    linked = 0
    for model, column in REFERENCES[a.record_type]:
        linked += await session.scalar(
            select(func.count()).select_from(model).where(getattr(model, column) == a.merged_id)
        ) or 0
    facts["linked_records"] = linked
    return facts


async def _touches(session: AsyncSession, a: MergeArgs) -> Touches:
    return Touches(accounts=tuple(await _account_ids(session, a)),
                   records=((a.record_type, a.survivor_id), (a.record_type, a.merged_id)))


async def _describe(session: AsyncSession, a: MergeArgs, facts: dict[str, Any]) -> str:
    model = MODELS[a.record_type]
    s, m = await session.get(model, a.survivor_id), await session.get(model, a.merged_id)

    def name(r) -> str:
        return (getattr(r, "name", None) or getattr(r, "full_name", None) or "?") if r else "?"

    return f"Merge {a.record_type} '{name(m)}' into '{name(s)}'"


async def _run(session: AsyncSession, ctx: AgentContext, a: MergeArgs) -> dict[str, Any]:
    event = await merge_records(session, a.record_type, a.survivor_id, a.merged_id,
                                actor=ctx.approved_by or ctx.actor,
                                automatic=ctx.approved_by is None)
    return {"merge_event_id": str(event.id), "survivor_id": str(a.survivor_id),
            "merged_id": str(a.merged_id)}


register_tool(ToolDef(
    key=MERGE_TOOL,
    title="Merge two duplicate records",
    description="Combines two records that are the same company or person. The other "
                "record is kept as 'merged' and the merge can be undone.",
    args=MergeArgs, run=_run,
    facts=("match_score", "account_tier", "linked_records", "record_type"),
    compute_facts=_facts, touches=_touches, describe=_describe,
    permission="steward.review",
))

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Data Steward",
    purpose="Keeps one clean record per company and person: finds duplicates, merges the "
            "ones that share a verified ID (GSTIN, PAN, CIN, email), and sends the rest to "
            "a person for review. Every merge can be undone.",
    tools=(MERGE_TOOL,),
    default_level=L2, max_level=L3,
    triggers=("account or contact created or changed", "CSV import"),
    model_tier=None,  # rule-based; meaning vectors come through the gateway's embedding tier
    permissions=("accounts.read", "steward.review"),
))
