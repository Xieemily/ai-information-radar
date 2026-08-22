from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adapters.rss import RSSAdapter
from app.database import Base, get_db, make_engine, make_session_factory
from app.routes.api import router as api_router
from app.routes.web import router as web_router
from app.services.importer import import_legacy_feeds


def create_app(database_url: str | None = None, rss_adapter: RSSAdapter | None = None) -> FastAPI:
    local_engine = make_engine(database_url)
    session_factory = make_session_factory(local_engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        Base.metadata.create_all(local_engine)
        configured_legacy = os.getenv("LEGACY_FEEDS_FILE")
        vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
        legacy_path = (
            Path(configured_legacy)
            if configured_legacy
            else Path(vault_path) / "Scripts" / "newsletter" / "feeds.txt"
            if vault_path
            else Path("feeds.txt")
        )
        with session_factory() as session:
            import_legacy_feeds(session, legacy_path)
        yield
        local_engine.dispose()

    application = FastAPI(title="AI Information Radar", version="0.1.0", lifespan=lifespan)
    application.state.rss_adapter = rss_adapter or RSSAdapter()
    application.state.session_factory = session_factory

    def get_local_db():
        with session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = get_local_db
    application.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    application.include_router(web_router)
    application.include_router(api_router)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
