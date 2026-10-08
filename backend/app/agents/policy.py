"""Policy rules (decision T1): structured rows, readable as sentences.

A rule matches a proposed action when its agent and action match (empty = any)
and every condition holds on the action's facts. A condition on a fact the action
does not provide never holds. Outcomes, strongest first: block > ask_person > allow.
"""

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AGENTS, FACTS, OPS, TOOLS
from app.core.format import inr
from app.models import PolicyRule, Role

OUTCOMES = {"allow": "let it act", "ask_person": "ask a person first", "block": "block it"}
STRENGTH = {"allow": 0, "ask_person": 1, "block": 2}
NUMERIC_OPS = ("gt", "gte", "lt", "lte", "eq", "ne")  # first one is the builder default
CHOICE_OPS = ("eq", "ne", "in", "not_in")


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def condition_holds(cond: dict[str, Any], facts: dict[str, Any]) -> bool:
    fact = FACTS.get(cond.get("fact", ""))
    if fact is None or cond["fact"] not in facts:
        return False
    actual, op, expected = facts[cond["fact"]], cond.get("op"), cond.get("value")
    if fact.type in ("number", "money"):
        a, e = _num(actual), _num(expected)
        if a is None or e is None:
            return False
        return {"lt": a < e, "lte": a <= e, "gt": a > e, "gte": a >= e, "eq": a == e,
                "ne": a != e}.get(op, False)
    values = expected if isinstance(expected, list) else [expected]
    actual = actual if actual is not None else ""
    return {"eq": actual == expected, "ne": actual != expected, "in": actual in values,
            "not_in": actual not in values}.get(op, False)


def rule_matches(rule: PolicyRule, agent_key: str, tool_key: str, facts: dict[str, Any]) -> bool:
    if not rule.enabled:
        return False
    if rule.agent_key and rule.agent_key != agent_key:
        return False
    if rule.tool_key and rule.tool_key != tool_key:
        return False
    return all(condition_holds(c, facts) for c in rule.conditions)


async def matching_rules(session: AsyncSession, agent_key: str, tool_key: str,
                         facts: dict[str, Any]) -> list[PolicyRule]:
    rules = await session.scalars(select(PolicyRule).where(PolicyRule.enabled.is_(True))
                                  .order_by(PolicyRule.created_at))
    return [r for r in rules if rule_matches(r, agent_key, tool_key, facts)]


def validate(data: dict[str, Any]) -> list[str]:
    """Problems with a rule as submitted from the Policies screen or API."""
    problems = []
    agent, tool = data.get("agent_key"), data.get("tool_key")
    if agent and agent not in AGENTS:
        problems.append(f"Unknown agent '{agent}'")
    if tool and tool not in TOOLS:
        problems.append(f"Unknown action '{tool}'")
    if agent in AGENTS and tool and tool not in AGENTS[agent].tools:
        problems.append(f"{AGENTS[agent].name} cannot use '{tool}'")
    if data.get("outcome") not in OUTCOMES:
        problems.append("Outcome must be allow, ask_person or block")
    if data.get("approver_role_id") and data.get("outcome") != "ask_person":
        problems.append("Only an 'ask a person' rule can name who decides")
    for c in data.get("conditions") or []:
        fact = FACTS.get(c.get("fact", ""))
        if fact is None:
            problems.append(f"Unknown condition '{c.get('fact')}'")
            continue
        ops = NUMERIC_OPS if fact.type in ("number", "money") else CHOICE_OPS
        if c.get("op") not in ops:
            problems.append(f"'{fact.label}' cannot use '{c.get('op')}'")
            continue
        value = c.get("value")
        if fact.type in ("number", "money") and _num(value) is None:
            problems.append(f"'{fact.label}' needs a number")
        if fact.type == "choice":
            values = value if isinstance(value, list) else [value]
            bad = [v for v in values if v not in fact.choices]
            if bad or not values:
                problems.append(f"'{fact.label}' must be one of {', '.join(fact.choices)}")
        if tool in TOOLS and fact.key not in TOOLS[tool].facts:
            problems.append(f"'{TOOLS[tool].title}' does not provide '{fact.label}'")
    return problems


def display_value(fact_key: str, value: Any) -> str:
    fact = FACTS[fact_key]
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    n = _num(value)
    if fact.type == "money" and n is not None:
        return inr(n)
    if fact.type == "number" and n is not None:
        return f"{n.normalize():f}"
    return str(value)


async def sentence(session: AsyncSession, rule: PolicyRule) -> str:
    who = AGENTS[rule.agent_key].name if rule.agent_key in AGENTS else "any agent"
    what = TOOLS[rule.tool_key].title.lower() if rule.tool_key in TOOLS else "do anything"
    parts = [f"When {who} wants to {what}"]
    for c in rule.conditions:
        if c.get("fact") in FACTS:
            label = FACTS[c["fact"]].label
            parts.append(f"{label[0].lower()}{label[1:]} {OPS.get(c['op'], c['op'])} "
                         f"{display_value(c['fact'], c.get('value'))}")
    text = " and ".join(parts) + ", " + OUTCOMES.get(rule.outcome, rule.outcome)
    if rule.approver_role_id:
        role = await session.get(Role, rule.approver_role_id)
        if role is not None:
            text += f" ({role.name} decides)"
    return text + "."


def strongest(rules: list[PolicyRule]) -> PolicyRule | None:
    return max(rules, key=lambda r: STRENGTH.get(r.outcome, 0), default=None)


def role_for(rules: list[PolicyRule]) -> uuid.UUID | None:
    """The person to ask: the first 'ask a person' rule that names a role."""
    return next((r.approver_role_id for r in rules
                 if r.outcome == "ask_person" and r.approver_role_id), None)
