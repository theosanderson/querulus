"""Database connection and session management"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import text

from querulus.config import config


# Global engine and session maker
engine = None
AsyncSessionLocal = None


async def init_db() -> None:
    """Initialize database connection pool"""
    global engine, AsyncSessionLocal

    engine = create_async_engine(
        config.settings.database_url,
        pool_size=config.settings.database_pool_size,
        max_overflow=config.settings.database_max_overflow,
        pool_pre_ping=True,  # Verify connections before using
        echo=False,  # Set to True for SQL logging during development
    )

    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def close_db() -> None:
    """Close database connection pool"""
    global engine
    if engine:
        await engine.dispose()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for FastAPI to get database session

    Usage:
        @app.get("/endpoint")
        async def endpoint(db: AsyncSession = Depends(get_db)):
            ...
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def health_check() -> tuple[bool, str | None]:
    """Check if database connection is healthy

    Returns:
        Tuple of (is_healthy, error_message)
    """
    # Parse database URL to show connection details (without exposing password)
    from urllib.parse import urlparse

    db_url = config.settings.database_url
    parsed = urlparse(db_url)

    # Create safe URL for logging (mask password)
    if parsed.password:
        safe_url = db_url.replace(parsed.password, "***")
    else:
        safe_url = db_url

    connection_info = (
        f"Database URL: {safe_url}\n"
        f"  Host: {parsed.hostname}\n"
        f"  Port: {parsed.port}\n"
        f"  Database: {parsed.path.lstrip('/') if parsed.path else 'N/A'}\n"
        f"  Username: {parsed.username or 'N/A'}\n"
        f"  Driver: {parsed.scheme}"
    )

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            if result.scalar() == 1:
                return True, None
            return False, "Database query returned unexpected result"
    except Exception as e:
        error_msg = (
            f"{type(e).__name__}: {str(e)}\n\n"
            f"Connection details:\n{connection_info}"
        )
        return False, error_msg
