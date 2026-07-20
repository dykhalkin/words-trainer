"""User, deck, and word lifecycle operations."""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import db
from .languages import (
    CardValidationError,
    normalize_deck_name,
    normalize_lemma,
    normalize_spaces,
    validate_word,
)
from .models import Noun, Verb, VerbPrep, Word

GENERAL_DECK_NAME = "General"
ARCHIVE_DECK_NAME = "Archive"
RESERVED_DECK_NAMES = {
    normalize_deck_name(GENERAL_DECK_NAME),
    normalize_deck_name(ARCHIVE_DECK_NAME),
}


class WordCard(BaseModel):
    """Strict structured card schema used by the agent tool and DB validation."""

    model_config = ConfigDict(extra="forbid")

    lemma: str = Field(min_length=1)
    kind: Literal["noun", "verb", "verb_prep", "other"]
    translation: str = Field(min_length=1)
    example: str = Field(min_length=1)
    pronunciation: str = Field(min_length=1)
    article: str | None = None
    singular: str | None = None
    plural_full: str | None = None
    conjugation: dict[str, list[str]] | None = None
    verb: str | None = None
    preposition: str | None = None
    case: str | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "WordCard":
        if self.kind == "noun" and not all((self.article, self.singular, self.plural_full)):
            raise ValueError("noun requires article, singular, and plural_full")
        if self.kind == "verb" and not self.conjugation:
            raise ValueError("verb requires conjugation")
        if self.kind == "verb_prep" and not all((self.verb, self.preposition, self.case)):
            raise ValueError("verb_prep requires verb, preposition, and case")
        return self

    def to_word(self, *, source_file: str = "agent") -> Word:
        common = dict(
            lemma=normalize_spaces(self.lemma),
            kind=self.kind,
            translation=normalize_spaces(self.translation),
            example=normalize_spaces(self.example),
            pronunciation=normalize_spaces(self.pronunciation),
            source_file=source_file,
        )
        if self.kind == "noun":
            return Noun(
                **common,
                article=normalize_spaces(self.article or "").lower(),
                singular=normalize_spaces(self.singular or ""),
                plural_full=normalize_spaces(self.plural_full or ""),
            )
        if self.kind == "verb":
            return Verb(**common, conjugation=self.conjugation or {})
        if self.kind == "verb_prep":
            return VerbPrep(
                **common,
                verb=normalize_spaces(self.verb or ""),
                preposition=normalize_spaces(self.preposition or ""),
                case=normalize_spaces(self.case or ""),
            )
        return Word(**common)


def validate_card(language: str, fields: dict[str, Any] | WordCard) -> tuple[WordCard, list[str]]:
    card = fields if isinstance(fields, WordCard) else WordCard.model_validate(fields)
    warnings = validate_word(card.to_word(), language, strict_agent=True)
    return card, warnings


async def _ensure_general_deck(
    conn: AsyncConnection[dict[str, Any]], user_id: int, language: str
) -> dict[str, Any]:
    result = await conn.execute(
        """INSERT INTO decks(user_id, language, name, normalized_name, is_general)
           VALUES (%s, %s, %s, %s, true)
           ON CONFLICT (user_id, language) WHERE is_general
           DO UPDATE SET user_id = EXCLUDED.user_id
           RETURNING *""",
        (user_id, language, GENERAL_DECK_NAME, normalize_deck_name(GENERAL_DECK_NAME)),
    )
    return await result.fetchone()


async def ensure_general_deck(database: db.Database, user_id: int, language: str) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            return await _ensure_general_deck(conn, user_id, language)


async def _ensure_archive_deck(
    conn: AsyncConnection[dict[str, Any]], user_id: int, language: str
) -> dict[str, Any]:
    result = await conn.execute(
        """INSERT INTO decks(
               user_id, language, name, normalized_name, is_archive
           ) VALUES (%s, %s, %s, %s, true)
           ON CONFLICT (user_id, language) WHERE is_archive
           DO UPDATE SET user_id = EXCLUDED.user_id
           RETURNING *""",
        (user_id, language, ARCHIVE_DECK_NAME, normalize_deck_name(ARCHIVE_DECK_NAME)),
    )
    return await result.fetchone()


async def ensure_archive_deck(database: db.Database, user_id: int, language: str) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            return await _ensure_archive_deck(conn, user_id, language)


async def bootstrap_user(
    database: db.Database,
    *,
    name: str,
    chat_id: int,
    language: str = "de",
    timezone: str = "Europe/Berlin",
    llm_monthly_cap_usd: float = 20.0,
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """INSERT INTO users(name, chat_id, timezone, llm_monthly_cap_usd)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET
                       chat_id = EXCLUDED.chat_id,
                       updated_at = now()
                   RETURNING *""",
                (name, chat_id, timezone, llm_monthly_cap_usd),
            )
            user = await result.fetchone()
            await _ensure_general_deck(conn, user["id"], language)
            return user


async def get_user(database: db.Database, identifier: str | int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        if isinstance(identifier, int):
            return await db.fetch_one(
                conn, "SELECT * FROM users WHERE id = %s OR chat_id = %s", (identifier, identifier)
            )
        return await db.fetch_one(conn, "SELECT * FROM users WHERE name = %s", (identifier,))


async def list_users(database: db.Database) -> list[dict[str, Any]]:
    async with database.connection() as conn:
        return await db.fetch_all(conn, "SELECT * FROM users ORDER BY id")


async def create_deck(
    database: db.Database, user_id: int, language: str, name: str
) -> dict[str, Any]:
    name = normalize_spaces(name)
    if normalize_deck_name(name) in RESERVED_DECK_NAMES:
        raise ValueError("General and Archive are reserved deck names")
    async with database.connection() as conn:
        async with conn.transaction():
            await _ensure_general_deck(conn, user_id, language)
            result = await conn.execute(
                """INSERT INTO decks(user_id, language, name, normalized_name)
                   VALUES (%s, %s, %s, %s) RETURNING *""",
                (user_id, language, name, normalize_deck_name(name)),
            )
            return await result.fetchone()


async def _find_deck(
    conn: AsyncConnection[dict[str, Any]], user_id: int, deck: int | str, language: str | None = None
) -> dict[str, Any] | None:
    if isinstance(deck, int):
        return await db.fetch_one(
            conn, "SELECT * FROM decks WHERE user_id = %s AND id = %s", (user_id, deck)
        )
    params: list[Any] = [user_id, normalize_deck_name(deck)]
    language_clause = ""
    if language:
        language_clause = " AND language = %s"
        params.append(language)
    return await db.fetch_one(
        conn,
        "SELECT * FROM decks WHERE user_id = %s AND normalized_name = %s"
        + language_clause
        + " ORDER BY id LIMIT 1",
        tuple(params),
    )


async def get_deck(
    database: db.Database, user_id: int, deck: int | str, language: str | None = None
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await _find_deck(conn, user_id, deck, language)


async def list_decks(database: db.Database, user_id: int) -> list[dict[str, Any]]:
    async with database.connection() as conn:
        return await db.fetch_all(
            conn,
            """SELECT d.*, count(w.id)::int AS word_count,
                      count(w.id) FILTER (
                          WHERE w.card_status = 'active' AND NOT d.is_archive
                      )::int AS active_word_count,
                      count(w.id) FILTER (
                          WHERE w.card_status = 'needs_fix'
                      )::int AS needs_fix_count
               FROM decks d LEFT JOIN words w ON w.deck_id = d.id
               WHERE d.user_id = %s GROUP BY d.id
               ORDER BY d.language, d.is_general DESC, d.is_archive, d.name""",
            (user_id,),
        )


async def rename_deck(database: db.Database, user_id: int, deck_id: int, name: str) -> dict[str, Any]:
    normalized_name = normalize_deck_name(name)
    if normalized_name in RESERVED_DECK_NAMES:
        raise ValueError("General and Archive are reserved deck names")
    async with database.connection() as conn:
        async with conn.transaction():
            deck = await _find_deck(conn, user_id, deck_id)
            if not deck:
                raise LookupError("deck not found")
            if deck["is_general"] or deck["is_archive"]:
                raise ValueError("general deck or archive deck cannot be renamed")
            result = await conn.execute(
                """UPDATE decks SET name = %s, normalized_name = %s, updated_at = now()
                   WHERE id = %s RETURNING *""",
                (normalize_spaces(name), normalized_name, deck_id),
            )
            return await result.fetchone()


async def delete_deck(database: db.Database, user_id: int, deck_id: int) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "SELECT * FROM decks WHERE id = %s AND user_id = %s FOR UPDATE",
                (deck_id, user_id),
            )
            deck = await result.fetchone()
            if not deck:
                raise LookupError("deck not found")
            if deck["is_general"] or deck["is_archive"]:
                raise ValueError("general deck or archive deck cannot be deleted")
            general = await _ensure_general_deck(conn, user_id, deck["language"])
            moved = await conn.execute(
                "UPDATE words SET deck_id = %s, modified_at = now() WHERE deck_id = %s",
                (general["id"], deck_id),
            )
            await conn.execute("DELETE FROM decks WHERE id = %s", (deck_id,))
            return {"deleted_deck_id": deck_id, "moved_words": moved.rowcount, "general_deck_id": general["id"]}


async def move_word(database: db.Database, user_id: int, word_id: int, deck_id: int) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """UPDATE words w SET deck_id = d.id, modified_at = now()
                   FROM decks d
                   WHERE w.id = %s AND w.user_id = %s
                     AND d.id = %s AND d.user_id = w.user_id AND d.language = w.language
                     AND NOT d.is_archive
                   RETURNING w.*""",
                (word_id, user_id, deck_id),
            )
            row = await result.fetchone()
            if not row:
                raise ValueError("word and target deck must belong to the same user and language")
            return row


async def _insert_or_reuse_word(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    language: str,
    deck_id: int,
    word: Word,
    move_existing: bool = False,
) -> tuple[dict[str, Any], bool]:
    validate_word(word, language, strict_agent=False)
    key = normalize_lemma(language, word.lemma)
    existing = await db.fetch_one(
        conn,
        "SELECT * FROM words WHERE user_id = %s AND language = %s AND lemma_key = %s FOR UPDATE",
        (user_id, language, key),
    )
    if existing:
        if move_existing and existing["deck_id"] != deck_id:
            result = await conn.execute(
                "UPDATE words SET deck_id = %s, modified_at = now() WHERE id = %s RETURNING *",
                (deck_id, existing["id"]),
            )
            existing = await result.fetchone()
        return existing, True
    result = await conn.execute(
        """INSERT INTO words(user_id, language, deck_id, lemma, lemma_key, kind, card)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (
            user_id,
            language,
            deck_id,
            word.lemma,
            key,
            word.kind,
            Jsonb(db.word_to_card(word)),
        ),
    )
    return await result.fetchone(), False


async def add_word(
    database: db.Database,
    *,
    user_id: int,
    language: str,
    deck_id: int,
    word: Word,
    move_existing: bool = False,
) -> tuple[dict[str, Any], bool]:
    async with database.connection() as conn:
        async with conn.transaction():
            return await _insert_or_reuse_word(
                conn,
                user_id=user_id,
                language=language,
                deck_id=deck_id,
                word=word,
                move_existing=move_existing,
            )


async def get_word(
    database: db.Database, user_id: int, query: int | str, language: str | None = None
) -> Word | None:
    async with database.connection() as conn:
        if isinstance(query, int):
            row = await db.fetch_one(
                conn, "SELECT * FROM words WHERE id = %s AND user_id = %s", (query, user_id)
            )
        else:
            params: list[Any] = [user_id, f"%{normalize_spaces(query)}%"]
            language_clause = ""
            if language:
                language_clause = " AND language = %s"
                params.append(language)
            row = await db.fetch_one(
                conn,
                "SELECT * FROM words WHERE user_id = %s AND lemma ILIKE %s"
                + language_clause
                + " ORDER BY length(lemma), id LIMIT 1",
                tuple(params),
            )
        return db.word_from_row(row) if row else None


async def _find_word_row(
    conn: AsyncConnection[dict[str, Any]],
    user_id: int,
    query: int | str,
    language: str | None = None,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock = " FOR UPDATE" if for_update else ""
    if isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
        return await db.fetch_one(
            conn,
            "SELECT * FROM words WHERE id = %s AND user_id = %s" + lock,
            (int(query), user_id),
        )
    params: list[Any] = [user_id, f"%{normalize_spaces(str(query))}%"]
    language_clause = ""
    if language:
        language_clause = " AND language = %s"
        params.append(language)
    return await db.fetch_one(
        conn,
        "SELECT * FROM words WHERE user_id = %s AND lemma ILIKE %s"
        + language_clause
        + " ORDER BY length(lemma), id LIMIT 1"
        + lock,
        tuple(params),
    )


async def _archive_word_row(
    conn: AsyncConnection[dict[str, Any]], row: dict[str, Any]
) -> dict[str, Any]:
    archive = await _ensure_archive_deck(conn, row["user_id"], row["language"])
    result = await conn.execute(
        """UPDATE words SET deck_id = %s, modified_at = now()
           WHERE id = %s RETURNING *""",
        (archive["id"], row["id"]),
    )
    updated = await result.fetchone()
    updated["archive_deck_id"] = archive["id"]
    return updated


async def archive_word(
    database: db.Database, user_id: int, query: int | str
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            row = await _find_word_row(conn, user_id, query, for_update=True)
            if not row:
                raise LookupError("word not found")
            updated = await _archive_word_row(conn, row)
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND word_id = %s AND status = 'open'""",
                (user_id, row["id"]),
            )
            return updated


async def restore_word(
    database: db.Database, user_id: int, query: int | str, deck_id: int
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            row = await _find_word_row(conn, user_id, query, for_update=True)
            if not row:
                raise LookupError("word not found")
            current = await _find_deck(conn, user_id, row["deck_id"], row["language"])
            if not current or not current["is_archive"]:
                raise ValueError("only an archived word can be restored")
            target = await _find_deck(conn, user_id, deck_id, row["language"])
            if not target or target["is_archive"]:
                raise ValueError("restore target must be an active same-language deck")
            result = await conn.execute(
                """UPDATE words SET deck_id = %s, modified_at = now()
                   WHERE id = %s RETURNING *""",
                (target["id"], row["id"]),
            )
            return await result.fetchone()


async def flag_word(
    database: db.Database,
    user_id: int,
    query: int | str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            row = await _find_word_row(conn, user_id, query, for_update=True)
            if not row:
                raise LookupError("word not found")
            result = await conn.execute(
                """UPDATE words SET card_status = 'needs_fix', needs_fix_at = now(),
                          needs_fix_reason = %s, needs_fix_task_id = NULL, modified_at = now()
                   WHERE id = %s RETURNING *""",
                (reason, row["id"]),
            )
            updated = await result.fetchone()
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND word_id = %s AND status = 'open'""",
                (user_id, row["id"]),
            )
            return updated


async def list_word_issues(
    database: db.Database,
    user_id: int,
    *,
    deck_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    deck_clause = " AND w.deck_id = %s" if deck_id is not None else ""
    params: list[Any] = [user_id]
    if deck_id is not None:
        params.append(deck_id)
    params.extend((limit, offset))
    async with database.connection() as conn:
        return await db.fetch_all(
            conn,
            """SELECT w.id AS word_id, w.lemma, w.language, w.card_status,
                      w.needs_fix_at, w.needs_fix_reason, w.needs_fix_task_id,
                      d.id AS deck_id, d.name AS deck_name, d.is_archive
               FROM words w JOIN decks d ON d.id = w.deck_id
               WHERE w.user_id = %s AND w.card_status = 'needs_fix'"""
            + deck_clause
            + " ORDER BY w.needs_fix_at DESC NULLS LAST, w.id LIMIT %s OFFSET %s",
            tuple(params),
        )


async def replace_word_card(
    database: db.Database,
    user_id: int,
    word_id: int,
    fields: dict[str, Any] | WordCard,
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            row = await _find_word_row(conn, user_id, word_id, for_update=True)
            if not row:
                raise LookupError("word not found")
            card, warnings = validate_card(row["language"], fields)
            word = card.to_word(source_file="manager")
            key = normalize_lemma(row["language"], word.lemma)
            result = await conn.execute(
                """UPDATE words SET lemma = %s, lemma_key = %s, kind = %s, card = %s,
                          card_status = 'active', needs_fix_at = NULL,
                          needs_fix_reason = NULL, needs_fix_task_id = NULL,
                          modified_at = now()
                   WHERE id = %s AND user_id = %s RETURNING *""",
                (word.lemma, key, word.kind, Jsonb(db.word_to_card(word)), word_id, user_id),
            )
            updated = await result.fetchone()
            await conn.execute(
                """UPDATE progress SET due_at = LEAST(COALESCE(due_at, now()), now()),
                          updated_at = now() WHERE word_id = %s""",
                (word_id,),
            )
            updated["warnings"] = warnings
            return updated


async def _task_word_for_disposition(
    conn: AsyncConnection[dict[str, Any]], user_id: int, task_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    result = await conn.execute(
        """SELECT t.*, w.language, w.deck_id, w.card_status
           FROM tasks t JOIN words w ON w.id = t.word_id
           WHERE t.id = %s AND t.user_id = %s FOR UPDATE OF t, w""",
        (task_id, user_id),
    )
    task = await result.fetchone()
    if not task or task["status"] != "open" or task["expires_at"] <= datetime.now(timezone.utc):
        return None
    word = await db.fetch_one(conn, "SELECT * FROM words WHERE id = %s", (task["word_id"],))
    return task, word


async def archive_task_word(
    database: db.Database, user_id: int, task_id: str
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        async with conn.transaction():
            pair = await _task_word_for_disposition(conn, user_id, task_id)
            if not pair:
                return None
            task, word = pair
            updated = await _archive_word_row(conn, word)
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE id = %s""",
                (task_id,),
            )
            return {"task_id": task_id, "word_id": word["id"], "word": word["lemma"], **updated}


async def flag_task_word(
    database: db.Database,
    user_id: int,
    task_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        async with conn.transaction():
            pair = await _task_word_for_disposition(conn, user_id, task_id)
            if not pair:
                return None
            task, word = pair
            result = await conn.execute(
                """UPDATE words SET card_status = 'needs_fix', needs_fix_at = now(),
                          needs_fix_reason = %s, needs_fix_task_id = %s, modified_at = now()
                   WHERE id = %s RETURNING *""",
                (reason or "reported during exercise", task_id, word["id"]),
            )
            updated = await result.fetchone()
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE id = %s""",
                (task_id,),
            )
            return {"task_id": task_id, "word_id": word["id"], "word": word["lemma"], **updated}


async def stage_cards(
    database: db.Database,
    *,
    user_id: int,
    language: str,
    deck_name: str,
    cards: list[dict[str, Any] | WordCard],
) -> dict[str, Any]:
    if normalize_deck_name(deck_name) in RESERVED_DECK_NAMES:
        raise ValueError("General and Archive are reserved deck names")
    validated = [validate_card(language, card)[0] for card in cards]
    pending_id = uuid.uuid4().hex
    async with database.connection() as conn:
        await conn.execute(
            """INSERT INTO pending_cards(id, user_id, language, deck_name, cards)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                pending_id,
                user_id,
                language,
                normalize_spaces(deck_name),
                Jsonb([card.model_dump(exclude_none=True) for card in validated]),
            ),
        )
    return {
        "pending_id": pending_id,
        "cards": len(validated),
        "deck_name": deck_name,
        "language": language,
    }


async def get_pending(
    database: db.Database, user_id: int, pending_id: str
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn,
            """SELECT * FROM pending_cards
               WHERE id = %s AND user_id = %s AND status = 'pending'""",
            (pending_id, user_id),
        )


async def commit_pending(
    database: db.Database, user_id: int, pending_id: str, *, move_existing: bool = False
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """SELECT * FROM pending_cards
                   WHERE id = %s AND user_id = %s AND status = 'pending' FOR UPDATE""",
                (pending_id, user_id),
            )
            pending = await result.fetchone()
            if not pending:
                raise LookupError("pending card proposal not found")
            deck = await _find_deck(conn, user_id, pending["deck_name"], pending["language"])
            if not deck:
                await _ensure_general_deck(conn, user_id, pending["language"])
                created = await conn.execute(
                    """INSERT INTO decks(user_id, language, name, normalized_name)
                       VALUES (%s, %s, %s, %s) RETURNING *""",
                    (
                        user_id,
                        pending["language"],
                        pending["deck_name"],
                        normalize_deck_name(pending["deck_name"]),
                    ),
                )
                deck = await created.fetchone()
            if deck["is_archive"]:
                raise ValueError("cannot commit cards into the archive deck")
            cards_raw = pending["cards"]
            if isinstance(cards_raw, str):
                cards_raw = json.loads(cards_raw)
            inserted = collisions = 0
            word_ids: list[int] = []
            for raw in cards_raw:
                card, _ = validate_card(pending["language"], raw)
                row, collision = await _insert_or_reuse_word(
                    conn,
                    user_id=user_id,
                    language=pending["language"],
                    deck_id=deck["id"],
                    word=card.to_word(),
                    move_existing=move_existing,
                )
                inserted += int(not collision)
                collisions += int(collision)
                word_ids.append(row["id"])
            await conn.execute(
                """UPDATE pending_cards SET status = 'committed', resolved_at = now()
                   WHERE id = %s""",
                (pending_id,),
            )
            return {
                "pending_id": pending_id,
                "deck_id": deck["id"],
                "inserted": inserted,
                "collisions": collisions,
                "word_ids": word_ids,
            }


async def reject_pending(database: db.Database, user_id: int, pending_id: str) -> bool:
    async with database.connection() as conn:
        result = await conn.execute(
            """UPDATE pending_cards SET status = 'rejected', resolved_at = now()
               WHERE id = %s AND user_id = %s AND status = 'pending'""",
            (pending_id, user_id),
        )
        return result.rowcount == 1


def word_as_dict(word: Word) -> dict[str, Any]:
    return dataclasses.asdict(word)
