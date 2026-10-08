from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import get_sessionmaker, tenant_session
from app.llm import gateway, routing
from app.llm.providers import ProviderResponse
from app.models import LlmCall, Tenant, TenantLlmBudget


class FakeProvider:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    async def chat(self, model, messages, **kw):
        self.calls += 1
        if self.fail:
            raise ConnectionError("provider down")
        return ProviderResponse(text=f"{self.name}:{model}", input_tokens=1000, output_tokens=500)


@pytest.fixture
def fake_providers(monkeypatch):
    p = {"a": FakeProvider("a", fail=True), "b": FakeProvider("b")}
    monkeypatch.setattr(gateway.providers_mod, "PROVIDERS", p)
    monkeypatch.setattr(
        routing, "ROUTES", {"fast": [routing.Route("a", "m1"), routing.Route("b", "m2")]}
    )
    monkeypatch.setattr(routing, "PRICES_PER_MTOK", {"b/m2": (10.0, 20.0)})
    return p


async def _tid(slug):
    async with get_sessionmaker()() as s:
        return (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id


async def test_falls_back_and_records_every_attempt(make_tenant, fake_providers):
    tid = await _tid((await make_tenant())[0])
    async with tenant_session(tid) as s:
        res = await gateway.complete(
            s, tid, task="t", tier="fast", messages=[{"role": "user", "content": "hi"}]
        )
    assert res.text == "b:m2"
    assert res.cost_usd == Decimal("0.02")  # (1000*10 + 500*20) per million
    async with tenant_session(tid) as s:
        rows = (await s.scalars(select(LlmCall).order_by(LlmCall.provider))).all()
    assert [(r.provider, r.status) for r in rows] == [("a", "error"), ("b", "ok")]


async def test_budget_blocks_calls(make_tenant, fake_providers):
    tid = await _tid((await make_tenant())[0])
    async with tenant_session(tid) as s:
        s.add(TenantLlmBudget(tenant_id=tid, monthly_budget_usd=Decimal("0.01")))
    async with tenant_session(tid) as s:
        await gateway.complete(s, tid, task="t", tier="fast", messages=[])  # spends 0.02
    with pytest.raises(gateway.BudgetExceeded):
        async with tenant_session(tid) as s:
            await gateway.complete(s, tid, task="t", tier="fast", messages=[])
    assert fake_providers["b"].calls == 1
