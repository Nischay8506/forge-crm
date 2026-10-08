"""Tests run against a real PostgreSQL (RLS cannot be tested on SQLite).

Set TEST_OWNER_URL / TEST_APP_URL to point elsewhere; the defaults match the
local setup described in docs/HANDOVER.md. The schema is rebuilt once per run.
"""

import os
import uuid

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

OWNER_URL = os.environ.get(
    "TEST_OWNER_URL", "postgresql+asyncpg://forge_owner:forge_owner@localhost:5432/forge_test"
)
APP_URL = os.environ.get(
    "TEST_APP_URL", "postgresql+asyncpg://forge_app:forge_app@localhost:5432/forge_test"
)
os.environ["DATABASE_URL"] = APP_URL
os.environ["MIGRATION_DATABASE_URL"] = OWNER_URL
os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"

from app.cli import create_tenant  # noqa: E402
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


def _alembic_cfg() -> Config:
    here = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    cfg.attributes["url"] = OWNER_URL
    return cfg


@pytest.fixture(scope="session", autouse=True)
async def migrated_db():
    engine = create_async_engine(OWNER_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    import asyncio

    await asyncio.to_thread(command.upgrade, _alembic_cfg(), "head")
    yield


@pytest.fixture
async def client():
    from app.db import dispose_engine
    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c
    await dispose_engine()


@pytest.fixture
async def make_tenant():
    """Creates a tenant with an Admin user; returns (slug, email, password)."""

    async def _make(slug: str | None = None):
        slug = slug or f"t{uuid.uuid4().hex[:8]}"
        email, pw = f"admin@{slug}.example.com", "Passw0rd!"
        await create_tenant(slug.title(), slug, email, "Admin", pw)
        return slug, email, pw

    return _make


async def login(client, slug: str, email: str, pw: str) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"company": slug, "email": email, "password": pw}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
