"""Agent version numbers (decision R8).

An agent's definition lives in code (decision T4). Its version number goes up
whenever what it is changes: its charter, its tools (and their versions, facts
and permissions), its levels, triggers, model tier, memory or permissions. The
number is found from a fingerprint of that definition, so it never depends on a
person remembering to bump it. Numbers are shared by every company, since every
company runs the same code. Levels, rules and kill-switches a company sets are not
part of the definition: changing them does not make a new version.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import TOOLS, AgentDef
from app.models import AgentVersion

# (agent_key, fingerprint) -> number, filled only from rows already committed.
_known: dict[tuple[str, str], int] = {}


def definition(agent: AgentDef) -> dict[str, Any]:
    tools = []
    for key in sorted(agent.tools):
        t = TOOLS[key]
        tools.append({"key": t.key, "version": t.version, "facts": sorted(t.facts),
                      "permission": t.permission, "side_effect": t.side_effect,
                      "evidence_class": t.evidence_class})
    return {"name": agent.name, "purpose": agent.purpose, "tools": tools,
            "default_level": agent.default_level, "max_level": agent.max_level,
            "triggers": sorted(agent.triggers), "model_tier": agent.model_tier,
            "memory_scopes": sorted(agent.memory_scopes),
            "charter_prompt": agent.charter_prompt, "permissions": sorted(agent.permissions)}


def fingerprint(defn: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(defn, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


async def current_version(session: AsyncSession, agent: AgentDef) -> int:
    defn = definition(agent)
    fp = fingerprint(defn)
    if (agent.key, fp) in _known:
        return _known[(agent.key, fp)]
    number = await session.scalar(select(AgentVersion.number).where(
        AgentVersion.agent_key == agent.key, AgentVersion.fingerprint == fp))
    if number is not None:
        _known[(agent.key, fp)] = number
        return number
    # First run of this definition anywhere: number it under a lock so two workers
    # starting at once agree.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                          {"k": f"agent_version:{agent.key}"})
    number = await session.scalar(select(AgentVersion.number).where(
        AgentVersion.agent_key == agent.key, AgentVersion.fingerprint == fp))
    if number is None:
        number = (await session.scalar(select(func.max(AgentVersion.number)).where(
            AgentVersion.agent_key == agent.key)) or 0) + 1
        session.add(AgentVersion(agent_key=agent.key, number=number, fingerprint=fp,
                                 definition=defn))
        await session.flush()
    return number


def describe_change(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """What changed from one version to the next, in plain words."""
    if before is None:
        return ["First version"]
    out = []
    labels = {"purpose": "Charter changed", "charter_prompt": "Instructions changed",
              "model_tier": "Model tier changed", "default_level": "Default level changed",
              "max_level": "Highest allowed level changed", "triggers": "Triggers changed",
              "memory_scopes": "Memory changed", "permissions": "Permissions changed",
              "name": "Name changed"}
    for key, label in labels.items():
        if before.get(key) != after.get(key):
            out.append(label)
    old = {t["key"]: t for t in before.get("tools", [])}
    new = {t["key"]: t for t in after.get("tools", [])}
    out += [f"Tool added: {k}" for k in new if k not in old]
    out += [f"Tool removed: {k}" for k in old if k not in new]
    out += [f"Tool changed: {k} (v{old[k]['version']} → v{new[k]['version']})"
            if old[k]["version"] != new[k]["version"] else f"Tool changed: {k}"
            for k in new if k in old and old[k] != new[k]]
    return out or ["No visible change"]
