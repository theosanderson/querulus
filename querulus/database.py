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


async def health_check() -> bool:
    """Check if database connection is healthy"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception:
        return False
