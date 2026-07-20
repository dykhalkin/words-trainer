"""Asynchronous PostgreSQL access and migrations for the vocabulary trainer."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .models import Noun, Verb, VerbPrep, Word

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = Path(__file__).with_name("migrations")
DEFAULT_DATABASE_URL = "postgresql://words_trainer:words_trainer_dev@127.0.0.1:55432/words_trainer"
WORD_CLASSES = {"noun": Noun, "verb": Verb, "verb_prep": VerbPrep, "other": Word}


class DatabaseUnavailable(RuntimeError):
    """Raised when PostgreSQL cannot become ready within the configured window."""


class Database:
    """Lifecycle wrapper around a psycopg async connection pool."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 6,
        open_timeout: float = 30.0,
    ) -> None:
        self.dsn = dsn
        self.open_timeout = open_timeout
        self.pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=10.0,
            kwargs={"row_factory": dict_row},
        )

    async def open(self, *, migrate: bool = True) -> None:
        """Open the pool with bounded non-blocking retries, then migrate."""
        deadline = asyncio.get_running_loop().time() + self.open_timeout
        delay = 0.25
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                probe = await AsyncConnection.connect(self.dsn, connect_timeout=3)
                await probe.close()
                break
            except Exception as exc:  # psycopg exposes several connection errors
                last_error = exc
                await asyncio.sleep(delay)
                delay = min(delay * 2, 3.0)
        else:
            raise DatabaseUnavailable("PostgreSQL is unavailable") from last_error

        await self.pool.open()
        try:
            await self.pool.wait(timeout=self.open_timeout)
            if migrate:
                await self.migrate()
        except Exception:
            await self.pool.close()
            raise

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection[dict[str, Any]]]:
        async with self.pool.connection() as conn:
            yield conn

    async def migrate(self) -> None:
        """Apply every numbered SQL migration exactly once."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations (
                           version TEXT PRIMARY KEY,
                           applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                       )"""
                )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('words_trainer_migrations'))"
                )
                result = await conn.execute("SELECT version FROM schema_migrations")
                applied = {row["version"] for row in await result.fetchall()}
                for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                    if path.name in applied:
                        continue
                    await conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                    await conn.execute(
                        "INSERT INTO schema_migrations(version) VALUES (%s)",
                        (path.name,),
                    )


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def from_env(**kwargs: Any) -> Database:
    return Database(database_url(), **kwargs)


def word_to_card(word: Word) -> dict[str, Any]:
    """Serialize a word without runtime-only attributes."""
    return dataclasses.asdict(word)


def canonical_card_json(card: Mapping[str, Any]) -> str:
    return json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def card_hash(card: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_card_json(card).encode("utf-8")).hexdigest()


def word_from_row(row: Mapping[str, Any]) -> Word:
    raw = row.get("card")
    card = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    cls = WORD_CLASSES.get(str(row.get("kind", card.get("kind", "other"))), Word)
    allowed = {field.name for field in dataclasses.fields(cls)}
    values = {key: value for key, value in card.items() if key in allowed}
    word = cls(**values)
    word.db_id = int(row["id"])  # type: ignore[attr-defined]
    word.user_id = int(row["user_id"])  # type: ignore[attr-defined]
    word.language = str(row["language"])  # type: ignore[attr-defined]
    word.deck_id = int(row["deck_id"])  # type: ignore[attr-defined]
    return word


async def fetch_one(
    conn: AsyncConnection[dict[str, Any]], query: str, params: tuple[Any, ...] = ()
) -> dict[str, Any] | None:
    result = await conn.execute(query, params)
    return await result.fetchone()


async def fetch_all(
    conn: AsyncConnection[dict[str, Any]], query: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    result = await conn.execute(query, params)
    return list(await result.fetchall())


async def sample_words(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    language: str,
    exclude_id: int,
    limit: int,
    kind: str | None = None,
) -> list[Word]:
    kind_clause = ""
    params: list[Any] = [user_id, language, exclude_id]
    if kind is not None:
        kind_clause = " AND kind = %s"
        params.append(kind)
    params.append(limit)
    rows = await fetch_all(
        conn,
        """SELECT * FROM words
           WHERE user_id = %s AND language = %s AND id <> %s"""
        + kind_clause
        + " ORDER BY random() LIMIT %s",
        tuple(params),
    )
    return [word_from_row(row) for row in rows]
