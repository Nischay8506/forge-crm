"""Counter (decision H2, safeguard 2): approve a changed version of the action.

The approver may change only the arguments the tool lists as counterable (e.g. the
amount). The changed action is checked by the policy gate as if newly proposed:
if a block rule or a stop applies, the counter is refused and nothing changes.
Otherwise the original card ends as "countered" (the audit trail keeps what was
asked and what was countered) and the changed action becomes a new card with its
own chain. The counter counts as this person's approval on the first step of the
new chain they could decide; every other step must still approve before anything
runs. If the changed action needs no person at all, it runs at once.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.agents import runtime
from app.agents.gate import evaluate
from app.agents.registry import AGENTS, TOOLS, AgentContext, Touches
from app.approvals.chain import Person, close_steps, conflict, direct, in_scope
from app.audit import chain
from app.core.errors import AppError, NotFound
from app.models import AgentRun, Decision, Delegation


class CounterRefused(AppError):
    def __init__(self, message: str, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = reasons or []


def changed_args(d: Decision, values: dict[str, Any]) -> dict[str, Any]:
    """The card's arguments with the countered values, checked by the tool's own
    argument rules. Only counterable fields may change, and at least one must."""
    tool = TOOLS.get(d.tool_key)
    allowed = tool.counter_fields if tool else ()
    if not allowed:
        raise CounterRefused("This kind of card cannot be countered")
    bad = sorted(set(values) - set(allowed))
    if bad:
        raise CounterRefused(f"Only {', '.join(allowed)} can be changed, not {', '.join(bad)}")
    try:
        parsed = tool.args.model_validate({**d.args, **values})
    except ValidationError as exc:
        raise CounterRefused(f"Not a valid value: {exc.errors()[0]['msg']}") from exc
    new = parsed.model_dump(mode="json")
    if all(new.get(f) == d.args.get(f) for f in allowed):
        raise CounterRefused("Change at least one value, or approve the card as it is")
    return new


async def counter(session, d_id: uuid.UUID, *, me: Person, acting_as: Person,
                  delegation: Delegation | None, values: dict[str, Any],
                  note: str | None, decision_ms: int | None, card: dict[str, Any],
                  card_hash: str) -> tuple[Decision, runtime.ToolOutcome]:
    d = await session.get(Decision, d_id, with_for_update=True, populate_existing=True)
    if d is None or d.kind != "approval":
        raise NotFound("Decision not found")
    if d.status != "pending":
        raise AppError(f"This decision is already {d.status}")
    tool = TOOLS[d.tool_key]
    new_args = changed_args(d, values)
    on_behalf_of = acting_as.actor if acting_as is not me else None
    note = (note or "").strip() or None

    # 1. The changed action through the gate first, changing nothing (safeguard 2).
    run = await session.get(AgentRun, d.run_id) if d.run_id else None
    ctx = AgentContext(tenant_id=d.tenant_id, agent=AGENTS[d.agent_key], run_id=d.run_id,
                       agent_version=run.agent_version if run else None)
    parsed = tool.args.model_validate(new_args)
    facts = await tool.compute_facts(session, parsed) if tool.compute_facts else {}
    touches = await tool.touches(session, parsed) if tool.touches else Touches()
    gate = await evaluate(session, ctx, tool, facts, touches)
    if gate.outcome == "block":
        raise CounterRefused("The rules block the changed version: " + "; ".join(gate.reasons),
                             gate.reasons)
    # A stand-in counters within the scope they were lent (safeguard 3).
    if delegation is not None and delegation.kind == "away" and "amount" in facts \
            and delegation.max_amount is not None \
            and float(facts["amount"]) > float(delegation.max_amount):
        raise CounterRefused("That is more than the out-of-office lets you approve")
    if delegation is not None and delegation.kind == "card" and "amount" in facts \
            and "amount" in d.facts and float(facts["amount"]) > float(d.facts["amount"]):
        raise CounterRefused("On a card handed to you, a counter cannot raise the amount")

    # 2. The original card ends as countered, keeping both versions in the trail.
    now = datetime.now(UTC)
    step = d.current_step
    if step is not None:
        step.status, step.decided_by, step.on_behalf_of = "countered", me.actor, on_behalf_of
        step.decided_at, step.note, step.decision_ms = now, note, decision_ms
        step.card_hash = card_hash
    close_steps(d, "cancelled")
    d.status, d.decided_by, d.decided_at, d.note, d.decision_ms = (
        "countered", me.actor, now, note, decision_ms)
    fields = sorted(k for k in values if new_args.get(k) != d.args.get(k))
    rec = await chain.append(session, d.tenant_id, "decision.countered",
                             **runtime._decision_kw(d, me.actor), data={
                                 "decision_id": d.id, "on_behalf_of": on_behalf_of,
                                 "step": d.step_index + 1, "steps": len(d.steps),
                                 "fields": fields,
                                 "asked": {k: d.args.get(k) for k in fields},
                                 "countered": {k: new_args.get(k) for k in fields},
                                 "note": note, "decision_ms": decision_ms,
                                 "card_hash": card_hash, "card": card,
                                 "recommended": d.recommended_option})

    # 3. The changed action as a new proposal, with its own chain. It keeps the agent's
    #    reasoning as context, but no recommended option: the agent recommended for the
    #    card as asked, not for the changed one (safeguard 8 counts overrides only
    #    against a recommendation the agent actually made).
    outcome = await runtime.call_tool(
        session, ctx, d.tool_key, new_args, recommendation=d.recommendation,
        recommend=None, requested_by=d.requested_by, counter_of=d.id)
    d.result = {"status": "countered", "result": {"countered_by_decision": str(
        outcome.decision_id) if outcome.decision_id else None}, "error": None,
        "reasons": [], "audit": {"decision": rec.seq, **outcome.audit}}
    if outcome.status != "waiting":
        d.result["result"]["outcome"] = outcome.status
        return d, outcome

    # 4. The counter is this person's approval on the first new step they could decide.
    #    The chain keeps its order: an earlier step still decides first (safeguard 1).
    #    Its decision time stays on the countered card only, so it is counted once.
    new = await session.get(Decision, outcome.decision_id, populate_existing=True)
    for s in new.steps:
        if _can(new, s, me, acting_as, delegation):
            if s.position == new.step_index:
                await runtime.decide(session, new.id, approve=True, actor=me.actor,
                                     note=note or "Approved as countered",
                                     decision_ms=None, on_behalf_of=on_behalf_of,
                                     card=card, card_hash=card_hash)
            else:
                await runtime.approve_ahead(session, new, s, actor=me.actor,
                                            on_behalf_of=on_behalf_of,
                                            note=note or "Approved as countered",
                                            card=card, card_hash=card_hash)
            break
    await session.flush()
    return d, outcome


def _can(d: Decision, step, me: Person, acting_as: Person, delegation: Delegation | None
         ) -> bool:
    """Could this person (for acting_as, when standing in) decide this step?"""
    if conflict(d, me.actor) or conflict(d, acting_as.actor):
        return False
    if direct(d, step, acting_as) is not None:
        return False
    if acting_as is me:
        return True
    return delegation is not None and (delegation.kind == "card"
                                       or in_scope(delegation, d, datetime.now(UTC)))
