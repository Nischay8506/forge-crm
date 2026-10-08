"""What a tenant controls: autonomy levels and overrides (T3), kill-switches,
and record takeovers (T8). Every change is written to the audit chain."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AGENTS, AgentDef
from app.audit import chain
from app.core.errors import AppError, NotFound
from app.models import (
    Account,
    AgentSetting,
    AutonomyOverride,
    KillSwitch,
    RecordTakeover,
)
from app.models.golden import ACCOUNT_TIERS


def agent_or_404(key: str) -> AgentDef:
    agent = AGENTS.get(key)
    if agent is None:
        raise NotFound(f"Unknown agent '{key}'")
    return agent


async def account_tiers(session: AsyncSession,
                        account_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Each account's tier. A site or company without its own tier takes the nearest
    parent's (site -> company -> group); with none anywhere it is 'standard'."""
    ids = {a for a in account_ids if a}
    rows: dict[uuid.UUID, tuple[str | None, uuid.UUID | None]] = {}
    todo = set(ids)
    for _ in range(4):  # group > entity > site is three levels deep
        todo -= rows.keys()
        if not todo:
            break
        for aid, tier, parent in (await session.execute(
                select(Account.id, Account.tier, Account.parent_id)
                .where(Account.id.in_(todo)))).all():
            rows[aid] = (tier, parent)
        todo = {p for t, p in rows.values() if not t and p}
    out = {}
    for aid in ids:
        cur, seen = aid, set()
        while cur in rows and cur not in seen:
            seen.add(cur)
            tier, parent = rows[cur]
            if tier:
                out[aid] = tier
                break
            cur = parent
        out.setdefault(aid, "standard")
    return out


async def tenant_level(session: AsyncSession, agent: AgentDef) -> int:
    row = await session.scalar(select(AgentSetting).where(AgentSetting.agent_key == agent.key))
    return min(row.autonomy_level if row else agent.default_level, agent.max_level)


async def effective_level(session: AsyncSession, agent: AgentDef,
                          account_ids: Iterable[uuid.UUID]) -> tuple[int, str]:
    """Level for an action touching these accounts, and where it came from.
    Per account: a named-account override, else its tier's override, else the
    agent's tenant level. Across accounts the lowest (most careful) level wins."""
    base = await tenant_level(session, agent)
    ids = [a for a in account_ids if a]
    if not ids:
        return base, "agent level"
    overrides = list(await session.scalars(
        select(AutonomyOverride).where(AutonomyOverride.agent_key == agent.key)))
    if not overrides:
        return base, "agent level"
    tiers = await account_tiers(session, ids)
    best: tuple[int, str] | None = None
    for aid in ids:
        # A merge can leave two named overrides on one account; the lower one applies.
        named = min((o.autonomy_level for o in overrides
                     if o.scope == "account" and o.account_id == aid), default=None)
        tier = tiers[aid]
        by_tier = next((o for o in overrides
                        if o.scope == "account_tier" and o.account_tier == tier), None)
        if named is not None:
            level, why = named, "override for this account"
        elif by_tier:
            level, why = by_tier.autonomy_level, f"override for {tier} accounts"
        else:
            level, why = base, "agent level"
        level = min(level, agent.max_level)
        if best is None or level < best[0]:
            best = (level, why)
    return best or (base, "agent level")


async def set_level(session: AsyncSession, tenant_id: uuid.UUID, agent: AgentDef, level: int,
                    actor: str) -> None:
    if not 0 <= level <= agent.max_level:
        raise AppError(f"{agent.name} can be set from L0 to L{agent.max_level}")
    row = await session.scalar(select(AgentSetting).where(AgentSetting.agent_key == agent.key))
    before = row.autonomy_level if row else agent.default_level
    if row is None:
        row = AgentSetting(tenant_id=tenant_id, agent_key=agent.key, autonomy_level=level,
                           updated_by=actor)
        session.add(row)
    row.autonomy_level, row.updated_by = level, actor
    await chain.append(session, tenant_id, "agent.level_changed", actor=actor,
                       agent_key=agent.key, data={"from": before, "to": level})


async def add_override(session: AsyncSession, tenant_id: uuid.UUID, agent: AgentDef,
                       data: dict[str, Any], actor: str) -> AutonomyOverride:
    scope, level = data.get("scope"), data.get("autonomy_level")
    if scope not in ("account_tier", "account"):
        raise AppError("scope must be account_tier or account")
    if not isinstance(level, int) or not 0 <= level <= agent.max_level:
        raise AppError(f"{agent.name} can be set from L0 to L{agent.max_level}")
    if scope == "account_tier" and data.get("account_tier") not in ACCOUNT_TIERS:
        raise AppError(f"account_tier must be one of {', '.join(ACCOUNT_TIERS)}")
    if scope == "account":
        acc = await session.get(Account, data.get("account_id"))
        if acc is None:
            raise NotFound("Account not found")
    existing = await session.scalar(select(AutonomyOverride).where(
        AutonomyOverride.agent_key == agent.key, AutonomyOverride.scope == scope,
        AutonomyOverride.account_tier == data.get("account_tier") if scope == "account_tier"
        else AutonomyOverride.account_id == data.get("account_id")))
    if existing is not None:
        raise AppError("An override for this already exists; remove it first")
    o = AutonomyOverride(tenant_id=tenant_id, agent_key=agent.key, scope=scope,
                         account_tier=data.get("account_tier") if scope == "account_tier" else None,
                         account_id=data.get("account_id") if scope == "account" else None,
                         autonomy_level=level, note=data.get("note"), created_by=actor)
    session.add(o)
    await session.flush()
    await chain.append(session, tenant_id, "agent.override_added", actor=actor,
                       agent_key=agent.key, data={"override_id": o.id, "scope": scope,
                                                  "account_tier": o.account_tier,
                                                  "account_id": o.account_id, "level": level})
    return o


async def remove_override(session: AsyncSession, tenant_id: uuid.UUID, override_id: uuid.UUID,
                          actor: str) -> None:
    o = await session.get(AutonomyOverride, override_id)
    if o is None:
        raise NotFound("Override not found")
    await chain.append(session, tenant_id, "agent.override_removed", actor=actor,
                       agent_key=o.agent_key, data={"override_id": o.id, "scope": o.scope,
                                                    "account_tier": o.account_tier,
                                                    "account_id": o.account_id,
                                                    "level": o.autonomy_level})
    await session.delete(o)


# Kill-switches ------------------------------------------------------------------

async def active_switches(session: AsyncSession) -> list[KillSwitch]:
    return list(await session.scalars(select(KillSwitch).where(KillSwitch.released_at.is_(None))
                                      .order_by(KillSwitch.created_at)))


async def stop(session: AsyncSession, tenant_id: uuid.UUID, scope: str, reason: str, actor: str,
               agent_key: str | None = None, account_id: uuid.UUID | None = None) -> KillSwitch:
    if scope not in ("tenant", "agent", "account"):
        raise AppError("scope must be tenant, agent or account")
    if not reason or not reason.strip():
        raise AppError("Say why agents are being stopped")
    if scope == "agent":
        agent_or_404(agent_key or "")
    if scope == "account" and await session.get(Account, account_id) is None:
        raise NotFound("Account not found")
    ks = KillSwitch(tenant_id=tenant_id, scope=scope, reason=reason.strip(), created_by=actor,
                    agent_key=agent_key if scope == "agent" else None,
                    account_id=account_id if scope == "account" else None)
    session.add(ks)
    await session.flush()
    await chain.append(session, tenant_id, "killswitch.on", actor=actor, agent_key=ks.agent_key,
                       record_type="account" if ks.account_id else None,
                       record_id=ks.account_id,
                       data={"kill_switch_id": ks.id, "scope": scope, "reason": ks.reason})
    return ks


async def release(session: AsyncSession, tenant_id: uuid.UUID, switch_id: uuid.UUID,
                  actor: str) -> KillSwitch:
    ks = await session.get(KillSwitch, switch_id)
    if ks is None or ks.released_at is not None:
        raise NotFound("Active kill-switch not found")
    ks.released_at, ks.released_by = datetime.now(UTC), actor
    await chain.append(session, tenant_id, "killswitch.off", actor=actor, agent_key=ks.agent_key,
                       record_type="account" if ks.account_id else None,
                       record_id=ks.account_id,
                       data={"kill_switch_id": ks.id, "scope": ks.scope})
    return ks


# Takeovers (T8) -------------------------------------------------------------------

TAKEOVER_TYPES = ("account", "contact")


async def active_takeovers(session: AsyncSession,
                           records: Iterable[tuple[str, uuid.UUID]]) -> list[RecordTakeover]:
    pairs = [(t, i) for t, i in records if i]
    if not pairs:
        return []
    return list(await session.scalars(select(RecordTakeover).where(
        RecordTakeover.released_at.is_(None),
        or_(*[(RecordTakeover.record_type == t) & (RecordTakeover.record_id == i)
              for t, i in pairs]))))


async def take_over(session: AsyncSession, tenant_id: uuid.UUID, record_type: str,
                    record_id: uuid.UUID, actor: str, reason: str | None) -> RecordTakeover:
    if record_type not in TAKEOVER_TYPES:
        raise AppError(f"record_type must be one of {', '.join(TAKEOVER_TYPES)}")
    if await active_takeovers(session, [(record_type, record_id)]):
        raise AppError("Someone has already taken this record over")
    t = RecordTakeover(tenant_id=tenant_id, record_type=record_type, record_id=record_id,
                       taken_by=actor, reason=(reason or "").strip() or None)
    session.add(t)
    await session.flush()
    await chain.append(session, tenant_id, "takeover.started", actor=actor,
                       record_type=record_type, record_id=record_id,
                       data={"takeover_id": t.id, "reason": t.reason})
    return t


async def hand_back(session: AsyncSession, tenant_id: uuid.UUID, takeover_id: uuid.UUID,
                    actor: str) -> RecordTakeover:
    t = await session.get(RecordTakeover, takeover_id)
    if t is None or t.released_at is not None:
        raise NotFound("Active takeover not found")
    t.released_at, t.released_by = datetime.now(UTC), actor
    await chain.append(session, tenant_id, "takeover.released", actor=actor,
                       record_type=t.record_type, record_id=t.record_id,
                       data={"takeover_id": t.id})
    return t
