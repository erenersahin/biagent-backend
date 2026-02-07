"""
BiAgent Database Module

Provides async SQLite database access with connection pooling.
"""

import aiosqlite
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
import json
from datetime import datetime
import uuid6

from .schema import SCHEMA_SQL, get_migration_sql

# Database file path
DB_PATH = os.environ.get("BIAGENT_DB_PATH", str(Path(__file__).parent.parent.parent / "data" / "biagent.db"))


def generate_id() -> str:
    """Generate a unique ID using UUID7 (time-ordered)."""
    return str(uuid6.uuid7())


def json_dumps(obj) -> str:
    """Serialize object to JSON string."""
    return json.dumps(obj, default=str)


def json_loads(s: str):
    """Parse JSON string to object."""
    return json.loads(s) if s else None


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Connect to the database and initialize schema."""
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        # Enable foreign keys
        await self._connection.execute("PRAGMA foreign_keys = ON")

        # Check existing schema before running CREATE statements
        # This allows us to run migrations for existing databases
        existing_tool_calls_cols = []
        try:
            cursor = await self._connection.execute("PRAGMA table_info(tool_calls)")
            rows = await cursor.fetchall()
            existing_tool_calls_cols = [row[1] for row in rows]  # Column name is at index 1
        except Exception:
            pass  # Table doesn't exist yet, that's fine

        # Run migrations BEFORE schema (so indexes on new columns work)
        # This adds columns to existing tables before CREATE IF NOT EXISTS runs
        if existing_tool_calls_cols:
            migration_sql = get_migration_sql(existing_tool_calls_cols)
            if migration_sql:
                await self._connection.executescript(migration_sql)
                await self._connection.commit()

        # Initialize schema (CREATE IF NOT EXISTS handles existing tables)
        await self._connection.executescript(SCHEMA_SQL)
        await self._connection.commit()

    async def disconnect(self):
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    @asynccontextmanager
    async def transaction(self):
        """Context manager for transactions."""
        try:
            yield self._connection
            await self._connection.commit()
        except Exception:
            await self._connection.rollback()
            raise

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Execute a SQL statement."""
        return await self._connection.execute(sql, params)

    async def executemany(self, sql: str, params_list: list) -> aiosqlite.Cursor:
        """Execute a SQL statement with multiple parameter sets."""
        return await self._connection.executemany(sql, params_list)

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Execute and fetch one row as dict."""
        cursor = await self._connection.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute and fetch all rows as list of dicts."""
        cursor = await self._connection.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def commit(self):
        """Commit the current transaction."""
        await self._connection.commit()


# Global database instance
_db: Optional[Database] = None


async def get_db() -> Database:
    """Get the database instance, creating if needed."""
    global _db
    if _db is None:
        _db = Database()
        await _db.connect()
    return _db


async def close_db():
    """Close the database connection."""
    global _db
    if _db:
        await _db.disconnect()
        _db = None


@asynccontextmanager
async def db_session() -> AsyncGenerator[Database, None]:
    """Context manager for database access."""
    db = await get_db()
    yield db


# Export
__all__ = [
    "Database",
    "get_db",
    "close_db",
    "db_session",
    "generate_id",
    "json_dumps",
    "json_loads",
    "DB_PATH",
]
