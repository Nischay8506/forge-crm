from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.agents.registry import load_definitions
from app.api.v1 import router as v1_router
from app.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.db import dispose_engine, get_sessionmaker
from app.integrations.mcp_server import build_server


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No background loops in the web process: pollers and timers run in the Celery worker.
    # The MCP server's session manager lives as long as the app.
    async with app.state.mcp.session_manager.run():
        yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    load_definitions()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(v1_router)

    # Outside AI tools (decision E9): MCP over streamable HTTP at /mcp/, bearer tokens
    # issued on the Integrations page.
    app.state.mcp = build_server()
    app.mount("/mcp", app.state.mcp.streamable_http_app(
        streamable_http_path="/", stateless_http=True, json_response=True, host="0.0.0.0"))

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, str]:
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
