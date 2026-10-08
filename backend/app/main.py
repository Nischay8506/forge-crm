from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import router as v1_router
from app.config import get_settings
from app.core.errors import AppError, app_error_handler
from app.db import dispose_engine, get_sessionmaker


@asynccontextmanager
async def lifespan(_: FastAPI):
    # No background loops in the web process: pollers and timers run in the Celery worker.
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
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
