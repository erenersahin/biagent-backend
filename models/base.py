"""
SQLAlchemy Base Model and Mixins

Provides base classes for all ORM models with common functionality.
"""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.ext.asyncio import AsyncAttrs, create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def generate_uuid() -> str:
    """Generate a UUID string for primary keys."""
    return str(uuid.uuid4())


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
        nullable=False
    )


class SoftDeleteMixin:
    """Mixin that adds soft delete functionality."""

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        default=None
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


# Database engine and session factory (configured at runtime)
_engine = None
_session_factory = None


def configure_database(database_url: str, echo: bool = False):
    """
    Configure the async database engine and session factory.

    Args:
        database_url: SQLAlchemy database URL (e.g., 'sqlite+aiosqlite:///./data/biagent.db')
        echo: Whether to echo SQL statements
    """
    global _engine, _session_factory

    _engine = create_async_engine(
        database_url,
        echo=echo,
        future=True,
    )

    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_async_session() -> AsyncSession:
    """Get an async database session."""
    if _session_factory is None:
        raise RuntimeError("Database not configured. Call configure_database() first.")

    async with _session_factory() as session:
        yield session


async def create_all_tables():
    """Create all tables in the database."""
    if _engine is None:
        raise RuntimeError("Database not configured. Call configure_database() first.")

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all_tables():
    """Drop all tables in the database (use with caution)."""
    if _engine is None:
        raise RuntimeError("Database not configured. Call configure_database() first.")

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


__all__ = [
    "Base",
    "TimestampMixin",
    "SoftDeleteMixin",
    "generate_uuid",
    "configure_database",
    "get_async_session",
    "create_all_tables",
    "drop_all_tables",
]
