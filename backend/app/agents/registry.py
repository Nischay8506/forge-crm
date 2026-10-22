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
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
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
    words: tuple[tuple[str, str], ...] = ()  # how a choice is shown to people


FACTS: dict[str, Fact] = {f.key: f for f in (
    Fact("amount", "Amount (INR)", "money"),
    Fact("discount_pct", "Discount %", "number"),
    Fact("margin_pct", "Margin %", "number"),
    Fact("floor_margin_pct", "Floor margin %", "number"),
    Fact("account_tier", "Account tier", "choice", ACCOUNT_TIERS),
    Fact("match_score", "Match score (0-100)", "number"),
    Fact("linked_records", "Linked records moved", "number"),
    Fact("record_type", "Record type", "choice", ("account", "contact")),
    Fact("is_tender", "Looks like a tender", "choice", ("yes", "no")),
    Fact("make_ability", "Can we make it", "choice",
         ("standard", "cto", "eto", "no_fit", "unchecked"),
         (("standard", "Standard"), ("cto", "Configurable"), ("eto", "Needs engineering"),
          ("no_fit", "No fit"), ("unchecked", "Not checked yet"))),
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
    # Retention class of this tool's audit details (R5): "customer_money" for what
    # customers are told and money (8 years by default), else "other" (3 years).
    evidence_class: str = "other"
    # Who may decide this tool's approval cards: people with this permission, and,
    # with four_eyes, never the person who asked (args["requested_by"]).
    decider_permission: str | None = None
    four_eyes: bool = False
    # Arguments a person may change when they counter (decision H2), e.g. ("amount",).
    # The changed action goes through the gate again before anything runs.
    counter_fields: tuple[str, ...] = ()
    # A rule that is part of the tool itself (not a tenant's policy rule): given the
    # facts, the reason a person must approve, or None. E.g. a tender's
    # acknowledgement is always drafted for a person (decision N7 A).
    ask_when: Callable[[dict[str, Any]], str | None] | None = None
    # A rule no one can approve past, given the facts: the reason it is blocked, or
    # None. E.g. a message about an enquiry that promises a price (decision N7 A).
    block_when: Callable[[dict[str, Any]], str | None] | None = None
    # Who approves this tool's cards, when that comes from a company's own rulebook
    # rather than from its policy rules: the roles in order, and the deadline. Given the
    # facts, so a countered action is chained by its own numbers (F3.4, Z3 A). The
    # discount matrix is the one rulebook like this today (app/cpq/doa.py).
    chain_for: Callable[[dict[str, Any]],
                        tuple[list[uuid.UUID], Decimal | None]] | None = None


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
    # Starting level per account tier, used until the company sets its own override
    # for that tier on the Agents screen, e.g. (("strategic", L0),) (decision N7 A).
    tier_levels: tuple[tuple[str, int], ...] = ()

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
DEFINITION_MODULES = ("app.steward.agent", "app.agents.sandbox", "app.integrations.agent",
                      "app.integrations.mcp_tools", "app.privacy.agent", "app.language.agent",
                      "app.enquiries.agent", "app.cpq.agent",
                      "app.cpq.doa_agent", "app.cpq.price_agent")


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
    agent_version: int | None = None  # R8: the agent definition's number (app/agents/versions)
    # R2: what the agent read since its last tool call (documents, passages, records),
    # kept with that call's audit details. Add with saw().
    seen: list[dict[str, Any]] = field(default_factory=list)

    @property
    def actor(self) -> str:
        return self.agent.actor

    def saw(self, source: str, content: Any, *, record_type: str | None = None,
            record_id: Any = None) -> None:
        """Note something the agent was given, e.g. a retrieved passage or document."""
        from datetime import UTC, datetime

        self.seen.append({"source": source, "record_type": record_type,
                          "record_id": str(record_id) if record_id else None,
                          "read_at": datetime.now(UTC).isoformat(), "content": content})

    def take_seen(self) -> list[dict[str, Any]]:
        seen, self.seen = self.seen, []
        return seen
