"""Engine / session factory helpers (PostgreSQL in dev, SQLite for tests)."""
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """SQLite does not enforce FKs by default; enable per connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_engine(database_url: str | None = None, echo: bool | None = None) -> Engine:
    """Create a SQLAlchemy engine; handles SQLite specifics automatically."""
    url = database_url or get_settings().database_url
    engine_kwargs: dict = {}
    if echo is not None:
        engine_kwargs["echo"] = echo
    if url.startswith("sqlite"):
        engine_kwargs.setdefault("connect_args", {"check_same_thread": False})
        if ":memory:" in url:
            engine_kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **engine_kwargs)
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    return create_engine(url, **engine_kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


# 默认应用会话(指向配置中的 DATABASE_URL;仅在实际使用时才建立连接)。
SessionLocal = create_session_factory(create_db_engine())


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()