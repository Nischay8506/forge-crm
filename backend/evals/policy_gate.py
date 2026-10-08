"""Chaos test for the policy gate (F12.2 acceptance: "100% of seeded threshold-
violating calls blocked with correct escalations").

The tenant gets the rules a finance head might write for the test-only Sandbox Agent:
  R1  amount is more than ₹1,00,000                         -> ask a person (Finance)
  R2  amount is more than ₹5,00,000                         -> block
  R3  account tier is strategic and amount is more than ₹25,000 -> ask a person (Finance)
and one autonomy override (key accounts: L0). Then N calls fire with random amounts
(many near the limits), accounts of every tier, autonomy levels L0-L3, and now and
then a kill-switch or a takeover. For each call an oracle written independently of
the gate says what must happen, and the eval checks what did happen:

  block (rule)  nothing written; action.blocked logged; a notice for a person
  block (stop)  nothing written; action.blocked logged (kill-switch or takeover)
  ask           nothing written; a pending approval routed to Finance (rules) or to
                anyone who decides (L0)
  act           the payment row is written; action.executed logged; a notice at L1

A sample of the waiting approvals is then approved and must execute exactly once,
and the tenant's audit chain must verify.

    uv run python -m evals.policy_gate --seed 11 --calls 400
"""

import argparse
import asyncio
import random
import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import controls, runtime
from app.agents.registry import AGENTS, load_definitions
from app.audit import chain
from app.models import Account, AuditRecord, Decision, PolicyRule, Role, SandboxAction

ASK_ABOVE, BLOCK_ABOVE, STRATEGIC_ASK_ABOVE = 100_000, 500_000, 25_000
TIERS = ("strategic", "key", "standard", None)
ACTOR = "eval:policy_gate"


@dataclass
class Call:
    n: int
    amount: Decimal
    tier: str | None
    level: int
    stop: str | None = None  # tenant | agent | account
    takeover: bool = False


@dataclass
class Expected:
    outcome: str  # act | ask_person | block
    hard: bool = False  # kill-switch / takeover: no notice
    role: str | None = None  # "finance" when a rule names Finance
    notice: bool = False


@dataclass
class Report:
    calls: int = 0
    violating: int = 0
    violating_ok: int = 0
    correct: int = 0
    approved_ok: int = 0
    approved: int = 0
    chain_ok: bool = False
    chain_checked: int = 0
    by_expected: Counter = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)


def oracle(c: Call) -> Expected:
    if c.stop or c.takeover:
        return Expected("block", hard=True)
    if c.amount > BLOCK_ABOVE:
        return Expected("block", notice=True)
    rule_ask = c.amount > ASK_ABOVE or (c.tier == "strategic" and c.amount > STRATEGIC_ASK_ABOVE)
    level = 0 if c.tier == "key" else c.level  # override: key accounts at L0
    if rule_ask:
        return Expected("ask_person", role="finance")
    if level == 0:
        return Expected("ask_person")
    return Expected("act", notice=level == 1)


def violates(c: Call) -> bool:
    return c.amount > ASK_ABOVE or (c.tier == "strategic" and c.amount > STRATEGIC_ASK_ABOVE)


def generate(seed: int, n: int) -> list[Call]:
    rng = random.Random(seed)
    edges = [STRATEGIC_ASK_ABOVE, ASK_ABOVE, BLOCK_ABOVE]
    calls = []
    for i in range(n):
        r = rng.random()
        if r < 0.45:  # right at a limit: one rupee either side, or exactly on it
            amount = Decimal(rng.choice(edges)) + Decimal(rng.choice(("-0.01", "0", "0.01", "1")))
        elif r < 0.85:
            amount = Decimal(rng.randint(1, 900_000)) + Decimal(rng.randint(0, 99)) / 100
        else:
            amount = Decimal(rng.randint(1, 20_000_000))
        x = rng.random()
        calls.append(Call(i, amount.quantize(Decimal("0.01")), rng.choice(TIERS),
                          rng.choice((0, 1, 2, 2, 3)),
                          stop=rng.choice(("tenant", "agent", "account")) if x < 0.06 else None,
                          takeover=0.06 <= x < 0.10))
    return calls


async def setup(session: AsyncSession, tenant_id: uuid.UUID) -> dict:
    load_definitions()
    finance = Role(tenant_id=tenant_id, name="Finance (eval)", permissions=["decisions.decide"])
    session.add(finance)
    accounts = {}
    for t in TIERS:
        a = Account(tenant_id=tenant_id, name=f"Gate eval {t or 'no tier'}", tier=t,
                    normalized_name=f"gate eval {t}")
        session.add(a)
        accounts[t] = a
    await session.flush()
    for conds, outcome, role in (
        ([{"fact": "amount", "op": "gt", "value": ASK_ABOVE}], "ask_person", finance.id),
        ([{"fact": "amount", "op": "gt", "value": BLOCK_ABOVE}], "block", None),
        ([{"fact": "account_tier", "op": "eq", "value": "strategic"},
          {"fact": "amount", "op": "gt", "value": STRATEGIC_ASK_ABOVE}], "ask_person", finance.id),
    ):
        session.add(PolicyRule(tenant_id=tenant_id, agent_key="sandbox",
                               tool_key="sandbox.record_payment", conditions=conds,
                               outcome=outcome, approver_role_id=role, created_by=ACTOR,
                               updated_by=ACTOR))
    await controls.add_override(session, tenant_id, AGENTS["sandbox"],
                                {"scope": "account_tier", "account_tier": "key",
                                 "autonomy_level": 0}, ACTOR)
    await session.flush()
    return {"finance": finance.id, "accounts": {t: a.id for t, a in accounts.items()}}


async def _count(session: AsyncSession, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where)) or 0


async def fire(session: AsyncSession, tenant_id: uuid.UUID, env: dict, c: Call,
               rep: Report) -> uuid.UUID | None:
    agent = AGENTS["sandbox"]
    account_id = env["accounts"][c.tier]
    await controls.set_level(session, tenant_id, agent, c.level, ACTOR)
    switch = takeover = None
    if c.stop:
        switch = await controls.stop(session, tenant_id, c.stop, "chaos test", ACTOR,
                                     agent_key="sandbox" if c.stop == "agent" else None,
                                     account_id=account_id if c.stop == "account" else None)
    if c.takeover:
        takeover = await controls.take_over(session, tenant_id, "account", account_id, ACTOR,
                                            "chaos test")
    rows_before = await _count(session, SandboxAction)
    notices_before = await _count(session, Decision, Decision.kind == "notice")
    ctx = await runtime.start_run(session, tenant_id, "sandbox", "chaos", str(c.n))
    out = await runtime.call_tool(session, ctx, "sandbox.record_payment",
                                  {"account_id": account_id, "amount": c.amount,
                                   "note": f"call {c.n}"})
    await runtime.finish_run(session, ctx, summary={"status": out.status})
    if switch:
        await controls.release(session, tenant_id, switch.id, ACTOR)
    if takeover:
        await controls.hand_back(session, tenant_id, takeover.id, ACTOR)

    exp = oracle(c)
    rep.by_expected[exp.outcome + (" (stop)" if exp.hard else "")] += 1
    wrote = await _count(session, SandboxAction) - rows_before
    notices = await _count(session, Decision, Decision.kind == "notice") - notices_before
    kinds = set(await session.scalars(select(AuditRecord.kind)
                                      .where(AuditRecord.run_id == ctx.run_id)))
    problems = []
    status = {"act": "done", "ask_person": "waiting", "block": "blocked"}[exp.outcome]
    if out.status != status:
        problems.append(f"status {out.status}, expected {status}")
    if exp.outcome != "act" and wrote:
        problems.append("a payment was written")
    if exp.outcome == "act" and wrote != 1:
        problems.append("the payment was not written")
    if (notices > 0) != exp.notice:
        problems.append(f"notice {'missing' if exp.notice else 'not expected'}")
    want = {"act": "action.executed", "ask_person": "decision.requested",
            "block": "action.blocked"}[exp.outcome]
    if not {"action.proposed", want} <= kinds:
        problems.append(f"audit missing {want}")
    if exp.outcome == "ask_person":
        d = await session.get(Decision, out.decision_id) if out.decision_id else None
        if d is None or d.status != "pending":
            problems.append("no pending approval")
        elif (d.approver_role_id == env["finance"]) != (exp.role == "finance"):
            problems.append("approval routed to the wrong person")
    ok = not problems
    rep.calls += 1
    rep.correct += ok
    if violates(c):
        rep.violating += 1
        rep.violating_ok += ok
    if not ok:
        rep.failures.append(f"call {c.n} ({c.amount}, tier {c.tier}, L{c.level}, "
                            f"stop {c.stop}, takeover {c.takeover}): {'; '.join(problems)}")
    return out.decision_id if ok and exp.outcome == "ask_person" else None


async def run(session: AsyncSession, tenant_id: uuid.UUID, seed: int, n: int,
              approve_sample: int = 10) -> Report:
    env = await setup(session, tenant_id)
    rep = Report()
    waiting = []
    for c in generate(seed, n):
        d = await fire(session, tenant_id, env, c, rep)
        if d:
            waiting.append(d)
    for decision_id in random.Random(seed).sample(waiting, min(approve_sample, len(waiting))):
        before = await _count(session, SandboxAction)
        d = await runtime.decide(session, decision_id, approve=True, actor=ACTOR)
        rep.approved += 1
        if d.status == "approved" and await _count(session, SandboxAction) - before == 1:
            rep.approved_ok += 1
        else:
            rep.failures.append(f"approving {decision_id} gave {d.status} {d.result}")
    await session.flush()
    res = await chain.verify(session, tenant_id)
    rep.chain_ok, rep.chain_checked = res.ok, res.checked
    if not res.ok:
        rep.failures.append(f"audit chain: {res.problem}")
    return rep


def format_report(rep: Report) -> str:
    def pct(a: int, b: int) -> str:
        return f"{100 * a / b:.1f}%" if b else "n/a"

    lines = [
        f"Calls fired: {rep.calls}",
        f"Threshold-violating calls handled correctly: {rep.violating_ok}/{rep.violating} "
        f"({pct(rep.violating_ok, rep.violating)})",
        f"All calls handled correctly: {rep.correct}/{rep.calls} ({pct(rep.correct, rep.calls)})",
        f"Approvals that then executed exactly once: {rep.approved_ok}/{rep.approved}",
        f"Audit chain: {'verified' if rep.chain_ok else 'BROKEN'} "
        f"({rep.chain_checked} records)",
        "", "| Expected | Calls |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(rep.by_expected.items())],
    ]
    if rep.failures:
        lines += ["", "Failures:", *rep.failures[:20]]
    return "\n".join(lines)


async def _main(seed: int, calls: int) -> None:
    from app.cli import create_tenant
    from app.db import dispose_engine, get_sessionmaker, tenant_session
    from app.models import Tenant

    slug = f"gate{uuid.uuid4().hex[:6]}"
    await create_tenant("Policy gate eval", slug, f"eval@{slug}.example.com", "Eval", "x" * 12)
    async with get_sessionmaker()() as s:
        tid = (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id
    async with tenant_session(tid) as s:
        rep = await run(s, tid, seed, calls)
    print(format_report(rep))
    await dispose_engine()
    if rep.failures:
        raise SystemExit(1)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--calls", type=int, default=400)
    a = p.parse_args()
    asyncio.run(_main(a.seed, a.calls))
