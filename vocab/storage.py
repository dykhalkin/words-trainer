"""Loading words from CSV sources (semicolon-separated, no header).

Row kind is detected per line:
- 22 columns -> verb with full conjugation (4 base + 6 Praesens + 6 Perfekt + 6 Praeteritum)
- first column like "der/die/das X (die Xe)" -> noun
- first column like "denken an + Akk" -> verb with preposition
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from . import db
from .languages import (
    normalize_deck_name,
    normalize_language_code,
    normalize_lemma,
    normalize_spaces,
    validate_word,
)
from .models import TENSES, Noun, Verb, VerbPrep, Word
from .words import RESERVED_DECK_NAMES, _ensure_general_deck, _find_deck, _insert_or_reuse_word

NOUN_RE = re.compile(r"^(der|die|das)\s+(.+?)\s*(?:\((.+?)\))?$", re.IGNORECASE)
PREP_RE = re.compile(r"^(.+?)\s+\+\s*(\w+)$")


def parse_row(row: list[str], source_file: str) -> Word | None:
    row = [c.strip() for c in row]
    if not row or not row[0]:
        return None
    base = row[:4] + [""] * (4 - len(row))
    lemma, translation, example, pronunciation = base[0], base[1], base[2], base[3]
    common = dict(
        translation=translation,
        example=example,
        pronunciation=pronunciation,
        source_file=source_file,
    )

    if len(row) >= 22:
        conjugation = {
            tense: row[4 + i * 6 : 10 + i * 6] for i, tense in enumerate(TENSES)
        }
        return Verb(lemma=lemma, kind="verb", conjugation=conjugation, **common)

    m = PREP_RE.match(lemma)
    if m:
        head, case = m.groups()
        parts = head.split()
        return VerbPrep(
            lemma=lemma,
            kind="verb_prep",
            verb=" ".join(parts[:-1]),
            preposition=parts[-1],
            case=case,
            **common,
        )

    m = NOUN_RE.match(lemma)
    if m:
        article, singular, plural = m.groups()
        return Noun(
            lemma=f"{article} {singular}",
            kind="noun",
            article=article,
            singular=singular,
            plural_full=plural or "",
            **common,
        )

    return Word(lemma=lemma, kind="other", **common)


def load_file(path: Path) -> list[Word]:
    words: list[Word] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f, delimiter=";"):
            word = parse_row(row, path.name)
            if word:
                words.append(word)
    return words


def load_dir(data_dir: Path) -> list[Word]:
    """Load all *.csv under data_dir (recursively), deduplicated by lemma (first wins)."""
    seen: dict[str, Word] = {}
    for path in sorted(data_dir.rglob("*.csv")):
        for word in load_file(path):
            seen.setdefault(word.lemma, word)
    return list(seen.values())


def word_to_row(word: Word) -> list[str]:
    if isinstance(word, Verb):
        cells = [cell for tense in TENSES for cell in word.conjugation.get(tense, [])]
        return [word.lemma, word.translation, word.example, word.pronunciation, *cells]
    if isinstance(word, Noun):
        label = word.lemma
        if word.plural_full:
            label = f"{label} ({word.plural_full})"
        return [label, word.translation, word.example, word.pronunciation]
    return [word.lemma, word.translation, word.example, word.pronunciation]


async def import_csv(
    database: db.Database,
    path: Path,
    *,
    user_id: int,
    deck_name: str,
    language: str,
) -> dict[str, Any]:
    language = normalize_language_code(language)
    if normalize_deck_name(deck_name) in RESERVED_DECK_NAMES:
        raise ValueError("General and Archive are reserved deck names")
    parsed = load_file(path)
    summary: dict[str, Any] = {
        "file": path.name,
        "deck": normalize_spaces(deck_name),
        "language": language,
        "rows": len(parsed),
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "conflicts": [],
    }
    async with database.connection() as conn:
        async with conn.transaction():
            await _ensure_general_deck(conn, user_id, language)
            deck = await _find_deck(conn, user_id, deck_name, language)
            if not deck:
                result = await conn.execute(
                    """INSERT INTO decks(user_id, language, name, normalized_name)
                       VALUES (%s, %s, %s, %s) RETURNING *""",
                    (
                        user_id,
                        language,
                        normalize_spaces(deck_name),
                        normalize_deck_name(deck_name),
                    ),
                )
                deck = await result.fetchone()
            if deck["is_archive"]:
                raise ValueError("cannot import into the archive deck")

            for word in parsed:
                validate_word(word, language, strict_agent=False)
                key = normalize_lemma(language, word.lemma)
                incoming_card = db.word_to_card(word)
                incoming_hash = db.card_hash(incoming_card)
                existing = await db.fetch_one(
                    conn,
                    """SELECT * FROM words
                       WHERE user_id = %s AND language = %s AND lemma_key = %s FOR UPDATE""",
                    (user_id, language, key),
                )
                if not existing:
                    inserted, _ = await _insert_or_reuse_word(
                        conn,
                        user_id=user_id,
                        language=language,
                        deck_id=deck["id"],
                        word=word,
                    )
                    await conn.execute(
                        """INSERT INTO word_import_state(word_id, imported_card_hash)
                           VALUES (%s, %s)""",
                        (inserted["id"], incoming_hash),
                    )
                    summary["added"] += 1
                    continue

                if existing["deck_id"] != deck["id"]:
                    summary["conflicts"].append(
                        {
                            "lemma": word.lemma,
                            "reason": "existing word belongs to another deck",
                            "word_id": existing["id"],
                            "deck_id": existing["deck_id"],
                        }
                    )
                    continue

                state = await db.fetch_one(
                    conn,
                    "SELECT * FROM word_import_state WHERE word_id = %s FOR UPDATE",
                    (existing["id"],),
                )
                live_card = existing["card"]
                if isinstance(live_card, str):
                    import json

                    live_card = json.loads(live_card)
                live_hash = db.card_hash(live_card)
                if not state or live_hash != state["imported_card_hash"]:
                    summary["conflicts"].append(
                        {
                            "lemma": word.lemma,
                            "reason": "database card changed after import or has no import baseline",
                            "word_id": existing["id"],
                            "deck_id": existing["deck_id"],
                        }
                    )
                    continue
                if incoming_hash == live_hash:
                    summary["unchanged"] += 1
                    continue
                await conn.execute(
                    """UPDATE words
                       SET lemma = %s, lemma_key = %s, kind = %s, card = %s,
                           card_status = 'active', needs_fix_at = NULL,
                           needs_fix_reason = NULL, needs_fix_task_id = NULL,
                           modified_at = now()
                       WHERE id = %s""",
                    (word.lemma, key, word.kind, Jsonb(incoming_card), existing["id"]),
                )
                await conn.execute(
                    """UPDATE word_import_state
                       SET imported_card_hash = %s, imported_at = now() WHERE word_id = %s""",
                    (incoming_hash, existing["id"]),
                )
                if existing["card_status"] == "needs_fix":
                    await conn.execute(
                        """UPDATE progress
                           SET due_at = LEAST(COALESCE(due_at, now()), now()),
                               updated_at = now()
                           WHERE word_id = %s""",
                        (existing["id"],),
                    )
                summary["updated"] += 1
            summary["deck_id"] = deck["id"]
    summary["conflict_count"] = len(summary["conflicts"])
    return summary


async def export_deck(
    database: db.Database, path: Path, *, user_id: int, deck_id: int
) -> dict[str, Any]:
    async with database.connection() as conn:
        deck = await db.fetch_one(
            conn, "SELECT * FROM decks WHERE id = %s AND user_id = %s", (deck_id, user_id)
        )
        if not deck:
            raise LookupError("deck not found")
        rows = await db.fetch_all(
            conn, "SELECT * FROM words WHERE deck_id = %s ORDER BY id", (deck_id,)
        )
    words = [db.word_from_row(row) for row in rows]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerows(word_to_row(word) for word in words)
    return {"deck_id": deck_id, "path": str(path), "rows": len(words)}
