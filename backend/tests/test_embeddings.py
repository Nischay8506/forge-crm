"""Meaning match (decision S2A): gateway embeddings and the review-only steward stage."""

import pytest
from sqlalchemy import select

from app.db import get_sessionmaker, tenant_session
from app.events import outbox
from app.llm import gateway, routing
from app.llm.providers import EmbeddingResponse
from app.models import AccountEmbedding, LlmCall, Tenant
from app.steward import embeddings, handlers
from tests.conftest import login

API = "/api/v1"


class FakeEmbeddingProvider:
    def __init__(self, fail=False):
        self.fail = fail

    async def embed(self, model, texts):
        if self.fail:
            raise ConnectionError("provider down")
        return EmbeddingResponse(vectors=[[float(len(t)), 1.0] for t in texts],
                                 input_tokens=10 * len(texts))


async def _tid(slug):
    async with get_sessionmaker()() as s:
        return (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id


async def test_gateway_embed_falls_back_and_records(make_tenant, monkeypatch):
    monkeypatch.setattr(gateway.providers_mod, "PROVIDERS",
                        {"a": FakeEmbeddingProvider(fail=True), "b": FakeEmbeddingProvider()})
    monkeypatch.setattr(routing, "ROUTES", {"embedding": [routing.Route("a", "e1"),
                                                          routing.Route("b", "e2")]})
    tid = await _tid((await make_tenant())[0])
    async with tenant_session(tid) as s:
        res = await gateway.embed(s, tid, task="t", texts=["abc", "abcdef"])
    assert res.vectors == [[3.0, 1.0], [6.0, 1.0]] and res.model_key == "b/e2"
    async with tenant_session(tid) as s:
        rows = (await s.scalars(select(LlmCall).order_by(LlmCall.provider))).all()
    assert [(r.provider, r.status, r.input_tokens) for r in rows] == [("a", "error", 0),
                                                                      ("b", "ok", 20)]


def test_embedder_is_off_without_a_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "gemini_api_key", None)
    assert embeddings.get_embedder() is None
    monkeypatch.setattr(get_settings(), "gemini_api_key", "x")
    assert isinstance(embeddings.get_embedder(), embeddings.GatewayEmbedder)


class FakeEmbedder:
    """Names with the same meaning get nearly the same vector; others are orthogonal."""

    VECTORS = {"Sunrise Polymers": [1.0, 0.05, 0.0], "Udaya Plastics": [1.0, 0.1, 0.0],
               "Moonlight Textiles": [0.0, 1.0, 0.0]}

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    async def embed(self, session, tenant_id, texts):
        self.calls += 1
        if self.fail:
            raise gateway.LlmError("All routes failed")
        return "fake/meaning", [self.VECTORS.get(t, [0.0, 0.0, 1.0]) for t in texts]


@pytest.fixture
async def steward(client, make_tenant):
    outbox.clear_handlers()
    handlers.register()
    slug, email, pw = await make_tenant()
    yield await login(client, slug, email, pw), await _tid(slug)
    embeddings.set_embedder(None)
    outbox.clear_handlers()


async def drain():
    while await outbox.dispatch_batch():
        pass


async def test_meaning_match_queues_for_review_never_merges(client, steward):
    steward, tid = steward
    embeddings.set_embedder(FakeEmbedder())
    for name in ("Sunrise Polymers", "Udaya Plastics", "Moonlight Textiles"):
        r = await client.post(f"{API}/accounts", headers=steward, json={"name": name})
        assert r.status_code == 201, r.text
    await drain()
    cands = (await client.get(f"{API}/steward/candidates", headers=steward)).json()
    assert len(cands) == 1 and cands[0]["band"] == "review"
    assert "embedding" in [x["signal"] for x in cands[0]["reasons"]]
    assert (await client.get(f"{API}/steward/merges", headers=steward)).json() == []
    async with tenant_session(tid) as s:
        stored = (await s.scalars(select(AccountEmbedding))).all()
    assert {e.model for e in stored} == {"fake/meaning"} and len(stored) == 3


async def test_embedding_failure_does_not_block_the_steward(client, steward):
    steward, tid = steward
    fake = FakeEmbedder(fail=True)
    embeddings.set_embedder(fake)
    r = await client.post(f"{API}/accounts", headers=steward, json={"name": "Sunrise Polymers"})
    assert r.status_code == 201
    await drain()
    assert fake.calls >= 1
    acc = (await client.get(f"{API}/accounts/{r.json()['id']}", headers=steward)).json()
    assert acc["account"]["hygiene_score"] is not None
    async with tenant_session(tid) as s:
        assert (await s.scalars(select(AccountEmbedding))).all() == []
        assert (await s.scalars(select(LlmCall))).all() == []  # fake embedder, no gateway
