from sqlalchemy import event, inspect, text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)
from app.core.config import settings


def _make_engine() -> AsyncEngine:
    url = settings.DATABASE_URL
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        # Desktop/dockerless rejim. SQLite bir faylda ishlaydi; WAL rejimi
        # backend va fon ishchisining bir vaqtda o'qish/yozishini yaxshilaydi.
        # NullPool: API va fon thread'lari turli event loop'larda ishlaydi,
        # ulanishlarni loop'lar orasida qayta ishlatmaslik kerak.
        engine = create_async_engine(
            url,
            echo=settings.DB_ECHO,
            pool_pre_ping=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        return engine

    connect_args: dict = {}
    if url.startswith("postgresql+asyncpg"):
        connect_args = {
            "server_settings": {
                "application_name": "videoworkflow-backend",
                "statement_timeout": str(settings.WORKER_JOB_TIMEOUT * 1000),
            }
        }
    return create_async_engine(
        url,
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        connect_args=connect_args,
    )


engine: AsyncEngine = _make_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _ensure_run_browser_column(connection) -> None:
    """Eski SQLite/PostgreSQL bazalariga run brauzeri ustunini qo'shadi."""
    columns = {
        column["name"] for column in inspect(connection).get_columns("runs")
    }
    if "browser" not in columns:
        connection.execute(text(
            "ALTER TABLE runs "
            "ADD COLUMN browser VARCHAR(32) DEFAULT 'chromium' NOT NULL"
        ))


def _ensure_job_source_column(connection) -> None:
    """Eski bazalarga job manbasi (video/recorded) ustunini qo'shadi."""
    columns = {
        column["name"] for column in inspect(connection).get_columns("video_jobs")
    }
    if "source" not in columns:
        connection.execute(text(
            "ALTER TABLE video_jobs "
            "ADD COLUMN source VARCHAR(32) DEFAULT 'video' NOT NULL"
        ))


async def init_db() -> None:
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_run_browser_column)
        await conn.run_sync(_ensure_job_source_column)
