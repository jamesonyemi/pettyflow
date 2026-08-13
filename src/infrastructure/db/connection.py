"""
PettyFlow Async Database Connection Manager
Provides SQLAlchemy async connection pooling, session scope management, and health checks.
"""

import os
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager

try:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

DEFAULT_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/pettyflow"

class DatabaseConnectionManager:
    """
    High-concurrency async connection pool manager for PostgreSQL/TimescaleDB.
    """
    def __init__(
        self,
        db_url: Optional[str] = None,
        pool_size: int = 20,
        max_overflow: int = 10,
        pool_timeout: int = 30,
        pool_recycle: int = 1800
    ):
        self.db_url = db_url or os.getenv("DATABASE_URL", DEFAULT_DB_URL)
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.pool_timeout = pool_timeout
        self.pool_recycle = pool_recycle
        self._engine = None
        self._session_factory = None

    def initialize(self):
        """Initialize the SQLAlchemy async engine and session factory."""
        if not HAS_SQLALCHEMY:
            raise RuntimeError("SQLAlchemy is required for DatabaseConnectionManager.")

        self._engine = create_async_engine(
            self.db_url,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_timeout=self.pool_timeout,
            pool_recycle=self.pool_recycle,
            echo=False
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    @property
    def engine(self):
        if self._engine is None and HAS_SQLALCHEMY:
            self.initialize()
        return self._engine

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator["AsyncSession", None]:
        """Async context manager for yielding transactional session scopes."""
        if self._session_factory is None:
            self.initialize()
        
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def health_check(self) -> bool:
        """Verify connection pool health by executing SELECT 1."""
        if not HAS_SQLALCHEMY or self.engine is None:
            return False
        try:
            async with self.engine.connect() as conn:
                res = await conn.execute(text("SELECT 1"))
                return res.scalar() == 1
        except Exception:
            return False

    async def close(self):
        """Gracefully dispose of connection pool."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
