"""Meaning match (decision S2A): the last matching stage.

Each active account (not sites) gets a vector of its name from the LLM gateway's
"embedding" tier (Gemini). Accounts whose vectors are very close (cosine similarity
>= matching.EMBEDDING_REVIEW) become *review* candidates even when no rule linked
them. A vector alone never auto-merges a pair.

Everything here is optional: without an embedding key the stage is skipped, and a
failed call is recorded by the gateway and skipped, never blocking the steward.
"""

import hashlib
import logging
import uuid
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import gateway
from app.models import Account, AccountEmbedding
from app.steward.normalize import normalize_name

log = logging.getLogger(__name__)

EMBED_TASK = "steward.embed"
BATCH_SIZE = 100


class Embedder(Protocol):
    async def embed(self, session: AsyncSession, tenant_id: uuid.UUID,
                    texts: list[str]) -> tuple[str, list[list[float]]]:
        """Returns (model key, one vector per text). Raises gateway.LlmError on failure."""
        ...


class GatewayEmbedder:
    async def embed(self, session: AsyncSession, tenant_id: uuid.UUID,
                    texts: list[str]) -> tuple[str, list[list[float]]]:
        res = await gateway.embed(session, tenant_id, task=EMBED_TASK, texts=texts)
        return res.model_key, res.vectors


_override: Embedder | None = None


def set_embedder(embedder: Embedder | None) -> None:
    """Tests (and later a local model) can plug in another embedder."""
    global _override
    _override = embedder


def get_embedder() -> Embedder | None:
    if _override is not None:
        return _override
    if not get_settings().steward_embeddings or not gateway.tier_available("embedding"):
        return None
    return GatewayEmbedder()


def embedding_text(a: Account) -> str:
    names = [a.name]
    if a.legal_name and normalize_name(a.legal_name) != normalize_name(a.name):
        names.append(a.legal_name)
    return " / ".join(names)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def ensure_embeddings(session: AsyncSession,
                            accounts: Sequence[Account]) -> dict[uuid.UUID, AccountEmbedding]:
    """Stored vectors for these accounts, computing missing or stale ones in batches."""
    embedder = get_embedder()
    eligible = [a for a in accounts if a.kind != "site" and a.status == "active"]
    if embedder is None or not eligible:
        return {}
    stored = {e.account_id: e for e in await session.scalars(
        select(AccountEmbedding).where(AccountEmbedding.account_id.in_([a.id for a in eligible])))}
    texts = {a.id: embedding_text(a) for a in eligible}
    todo = [a for a in eligible
            if a.id not in stored or stored[a.id].text_hash != _hash(texts[a.id])]
    for start in range(0, len(todo), BATCH_SIZE):
        chunk = todo[start:start + BATCH_SIZE]
        try:
            model, vectors = await embedder.embed(session, chunk[0].tenant_id,
                                                  [texts[a.id] for a in chunk])
        except gateway.LlmError as exc:
            log.warning("embedding skipped for %d accounts: %s", len(todo) - start, exc)
            break
        for a, vec in zip(chunk, vectors, strict=True):
            row = stored.get(a.id)
            if row is None:
                row = AccountEmbedding(account_id=a.id, tenant_id=a.tenant_id)
                session.add(row)
                stored[a.id] = row
            row.model, row.dims, row.embedding = model, len(vec), vec
            row.text_hash = _hash(texts[a.id])
        await session.flush()
    return stored


async def meaning_neighbours(session: AsyncSession, a: Account,
                             limit: int | None = None) -> dict[uuid.UUID, float]:
    """Closest active accounts of the same kind by meaning: {account id: similarity}."""
    if a.kind == "site" or a.status != "active":
        return {}
    emb = (await ensure_embeddings(session, [a])).get(a.id)
    if emb is None:
        return {}
    dist = AccountEmbedding.embedding.cosine_distance(emb.embedding)
    rows = await session.execute(
        select(AccountEmbedding.account_id, (1 - dist).label("similarity"))
        .join(Account, Account.id == AccountEmbedding.account_id)
        .where(AccountEmbedding.model == emb.model, AccountEmbedding.dims == emb.dims,
               AccountEmbedding.account_id != a.id, Account.status == "active",
               Account.kind == a.kind)
        .order_by(dist)
        .limit(limit or get_settings().steward_embedding_neighbours))
    return {aid: float(sim) for aid, sim in rows.all()}
