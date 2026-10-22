"""The policy gate: evaluated before every side-effecting tool call.

Checks, in order (each recorded in the audit chain with its result):
  1. charter     the tool is in the agent's charter and the agent holds its permission
  2. kill-switch no switch is on for the tenant, the agent or a touched account
  3. takeover    no touched record is taken over by a person (T8)
  4. rules       policy rules (T1): block > ask a person > allow
  5. autonomy    the effective level (T3): L0 asks a person, L1 acts and notifies,
                 L2/L3 act within policy
Outcome: act | ask_person | block. After a person approves (resume), steps 1-3
and block rules still apply; "ask a person" is satisfied by the approval.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import controls, policy
from app.agents.registry import L0, L1, LEVELS, AgentContext, ToolDef, Touches


@dataclass
class GateResult:
    outcome: str  # act | ask_person | block
    level: int | None = None
    notify: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # why a person or a block
    approver_role_id: uuid.UUID | None = None
    approval_steps: list[uuid.UUID] = field(default_factory=list)  # H1: roles, in order
    sla_hours: Decimal | None = None  # H4: the rules' deadline, if they set one
    rule_ids: list[str] = field(default_factory=list)
    hard_block: bool = False  # charter, kill-switch or takeover: no one can approve it

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})


async def evaluate(session: AsyncSession, ctx: AgentContext, tool: ToolDef,
                   facts: dict[str, Any], touches: Touches, *, approved: bool = False
                   ) -> GateResult:
    agent = ctx.agent
    g = GateResult(outcome="act")

    # 1. charter
    if tool.key not in agent.tools:
        g.check("charter", False, f"'{tool.title}' is not in {agent.name}'s charter")
    elif tool.permission and tool.permission not in agent.permissions:
        g.check("charter", False, f"{agent.name} lacks permission {tool.permission}")
    else:
        g.check("charter", True, f"'{tool.title}' is in {agent.name}'s charter")
    if not g.checks[-1]["ok"]:
        return _hard_block(g)

    # 2. kill-switches
    accounts = {a for a in touches.accounts if a}
    hit = None
    for ks in await controls.active_switches(session):
        if (ks.scope == "tenant" or (ks.scope == "agent" and ks.agent_key == agent.key)
                or (ks.scope == "account" and ks.account_id in accounts)):
            hit = ks
            break
    if hit:
        where = {"tenant": "all agents", "agent": agent.name,
                 "account": "agents on this account"}[hit.scope]
        g.check("kill_switch", False, f"Stopped: {where} ({hit.reason})")
        return _hard_block(g)
    g.check("kill_switch", True, "No kill-switch is on")

    # 3. takeovers
    records = list(touches.records) + [("account", a) for a in accounts]
    taken = await controls.active_takeovers(session, records)
    if taken:
        g.check("takeover", False, f"A person has taken over this {taken[0].record_type}")
        return _hard_block(g)
    g.check("takeover", True, "No person has taken these records over")

    if not tool.side_effect:
        g.check("policy", True, "Read-only action; no policy needed")
        return g

    # 4. policy rules, after the tool's own hard rule (no one can approve past it)
    built_in_block = tool.block_when(facts) if tool.block_when else None
    if built_in_block:
        g.check("policy", False, built_in_block)
        return _hard_block(g)
    rules = await policy.matching_rules(session, agent.key, tool.key, facts)
    g.rule_ids = [str(r.id) for r in rules]
    sentences = [await policy.sentence(session, r) for r in rules]
    top = policy.strongest(rules)
    if top is not None and top.outcome == "block":
        g.check("policy", False, "; ".join(s for r, s in zip(rules, sentences, strict=True)
                                           if r.outcome == "block"))
        g.outcome = "block"
        g.reasons = [s for r, s in zip(rules, sentences, strict=True) if r.outcome == "block"]
        return g
    ask_rules = [s for r, s in zip(rules, sentences, strict=True) if r.outcome == "ask_person"]
    built_in = tool.ask_when(facts) if tool.ask_when else None
    if built_in:
        ask_rules.append(built_in)
    if ask_rules and not approved:
        g.check("policy", False, "; ".join(ask_rules))
        g.outcome, g.reasons = "ask_person", list(ask_rules)
        g.approval_steps = policy.chain_for(rules)
        g.sla_hours = policy.sla_for(rules)
        # A tool whose approvers are a company's own rulebook says so itself (F3.4): the
        # discount matrix, not a policy rule, decides who signs a discount off. It is
        # read from the facts, so a countered discount is chained by its own numbers and
        # cannot slip through on the chain the first ask happened to need.
        if tool.chain_for is not None:
            steps, sla = tool.chain_for(facts)
            if steps:
                g.approval_steps = steps
            if sla is not None:
                g.sla_hours = sla
        g.approver_role_id = g.approval_steps[0] if g.approval_steps else None
    else:
        g.check("policy", True, "; ".join(sentences) if sentences else "No rule applies")

    # 5. autonomy
    level, source = await controls.effective_level(session, agent, accounts)
    g.level = level
    label = LEVELS[level].split(":")[0]
    if approved:
        g.check("autonomy", True, f"{label} ({source}); approved by a person")
        g.outcome = "act"
        return g
    if level == L0:
        g.check("autonomy", False, f"{label} ({source}): a person approves every action")
        g.outcome = "ask_person"
        g.reasons.append(f"{agent.name} is at {LEVELS[L0]}")
        return g
    g.check("autonomy", True, f"{label} ({source})")
    g.notify = level == L1 and g.outcome == "act"
    return g


def _hard_block(g: GateResult) -> GateResult:
    g.outcome, g.hard_block = "block", True
    g.reasons = [g.checks[-1]["detail"]]
    return g
