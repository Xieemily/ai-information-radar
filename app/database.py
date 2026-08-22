from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.getenv("DATABASE_URL", "sqlite:///./radar.db")
    if url.startswith("sqlite:///") and ":memory:" not in url:
        database_path = Path(url.removeprefix("sqlite:///")).expanduser()
        database_path.parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


engine = make_engine()
SessionLocal = make_session_factory(engine)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
