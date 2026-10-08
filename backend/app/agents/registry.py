"""Agent and tool definitions (decision T4: in code, reviewed like any change).

An agent's charter says what it is for, which tools it may call, its default and
maximum autonomy and its model tier. A tool is a typed, versioned action. Each
side-effecting tool also says which *facts* the policy gate may test (computed by
the tool from its own arguments, so an agent cannot misreport them) and which
records it touches (for kill-switches and takeovers).

Tenants change only levels, kill-switches and policy rules (app/agents/*.py and
the Agents screens), never these definitions.
"""

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.golden import ACCOUNT_TIERS

# Autonomy ladder (requirements 1.4)
L0, L1, L2, L3 = 0, 1, 2, 3
LEVELS = {
    L0: "L0 Draft only: a person approves every action",
    L1: "L1 Act and notify: acts, then tells a person",
    L2: "L2 Act within policy: acts on its own inside the rules",
    L3: "L3 Orchestrate: may also direct other agents",
}


@dataclass(frozen=True)
class Fact:
    """Something a policy rule can test (decision T1: conditions come from this list)."""

    key: str
    label: str
    type: str  # number | money | choice | text
    choices: tuple[str, ...] = ()


FACTS: dict[str, Fact] = {f.key: f for f in (
    Fact("amount", "Amount (INR)", "money"),
    Fact("discount_pct", "Discount %", "number"),
    Fact("account_tier", "Account tier", "choice", ACCOUNT_TIERS),
    Fact("match_score", "Match score (0-100)", "number"),
    Fact("linked_records", "Linked records moved", "number"),
    Fact("record_type", "Record type", "choice", ("account", "contact")),
)}

OPS: dict[str, str] = {
    "lt": "is less than", "lte": "is at most", "gt": "is more than", "gte": "is at least",
    "eq": "is", "ne": "is not", "in": "is one of", "not_in": "is none of",
}


@dataclass(frozen=True)
class Touches:
    """Records an action would change, checked against kill-switches and takeovers."""

    accounts: tuple[Any, ...] = ()
    records: tuple[tuple[str, Any], ...] = ()  # (record_type, id)


@dataclass(frozen=True)
class ToolDef:
    key: str
    title: str  # "Merge two duplicate records"
    description: str
    args: type[BaseModel]
    run: Callable[[AsyncSession, "AgentContext", Any], Awaitable[dict[str, Any]]]
    side_effect: bool = True
    facts: tuple[str, ...] = ()
    compute_facts: Callable[[AsyncSession, Any], Awaitable[dict[str, Any]]] | None = None
    touches: Callable[[AsyncSession, Any], Awaitable[Touches]] | None = None
    # One sentence for decision cards and the audit log, e.g. "Merge A into B".
    describe: Callable[[AsyncSession, Any, dict[str, Any]], Awaitable[str]] | None = None
    permission: str | None = None  # RBAC code the agent's service identity uses
    version: int = 1


@dataclass(frozen=True)
class AgentDef:
    key: str
    name: str
    purpose: str  # plain-language charter shown on the admin screen
    tools: tuple[str, ...]
    default_level: int
    max_level: int
    triggers: tuple[str, ...] = ()
    model_tier: str | None = None  # None = rule-based, no model calls
    memory_scopes: tuple[str, ...] = ()
    charter_prompt: str = ""
    test_only: bool = False
    permissions: tuple[str, ...] = field(default=())

    @property
    def actor(self) -> str:
        """Service identity: agents never act with a person's credentials."""
        return f"agent:{self.key}"


AGENTS: dict[str, AgentDef] = {}
TOOLS: dict[str, ToolDef] = {}


def register_tool(tool: ToolDef) -> ToolDef:
    if tool.key in TOOLS and TOOLS[tool.key] is not tool:
        raise ValueError(f"Tool {tool.key} registered twice")
    for f in tool.facts:
        if f not in FACTS:
            raise ValueError(f"Tool {tool.key} declares unknown fact {f}")
    TOOLS[tool.key] = tool
    return tool


def register_agent(agent: AgentDef) -> AgentDef:
    for t in agent.tools:
        if t not in TOOLS:
            raise ValueError(f"Agent {agent.key} uses unregistered tool {t}")
    if not (L0 <= agent.default_level <= agent.max_level <= L3):
        raise ValueError(f"Agent {agent.key} has invalid autonomy levels")
    AGENTS[agent.key] = agent
    return agent


# Modules that register agents and tools when imported. Add new agents here.
DEFINITION_MODULES = ("app.steward.agent", "app.agents.sandbox")


def load_definitions() -> None:
    """Import every agent definition (idempotent). Called by the API, the worker and
    the runtime so the registry is the same in every process."""
    for module in DEFINITION_MODULES:
        importlib.import_module(module)


@dataclass
class AgentContext:
    """Who is acting, in which tenant and run."""

    tenant_id: Any
    agent: AgentDef
    run_id: Any = None
    approved_by: str | None = None  # set when a person approved this call

    @property
    def actor(self) -> str:
        return self.agent.actor
