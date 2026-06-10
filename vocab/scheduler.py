"""Adaptive scheduling: which word to review next and with which exercise type.

Stage -> preferred exercise types (harder types for deeper stages):
    0 (new/lapsed)   choice
    1 (learning)     flashcard DE->RU
    2 (consolidate)  flashcard RU->DE, cloze
    3 (mature)       cloze, grammar, flashcard RU->DE
"""

from __future__ import annotations

import json
import random
import sqlite3
import uuid

from . import db, srs
from .exercises import GENERATORS
from .models import Word

STAGE_TYPES = {
    0: ["choice"],
    1: ["flashcard_de_ru"],
    2: ["flashcard_ru_de", "cloze"],
    3: ["cloze", "grammar", "flashcard_ru_de"],
}
FALLBACK = ["flashcard_de_ru", "choice"]
DEFAULT_NEW_PER_SESSION = 5
STARTED_SCAN_LIMIT = 100


def _stage_from_row(row) -> int:
    return srs.stage(row["reps"], row["interval_days"])


def pick_word(conn: sqlite3.Connection, rng: random.Random) -> tuple[Word, int] | None:
    """Next word to practice and its stage. Due reviews first, then new words."""
    due = db.fetch_due(conn, limit=10)
    if due:
        row = due[0]
        word = db.word_from_row(row)
        return word, _stage_from_row(row)
    new = db.fetch_new(conn, limit=DEFAULT_NEW_PER_SESSION)
    if new:
        row = rng.choice(new)
        return db.word_from_row(row), 0
    return None


def pick_new_word(conn: sqlite3.Connection, rng: random.Random) -> tuple[Word, int] | None:
    """A word with no progress yet."""
    new = db.fetch_new(conn, limit=DEFAULT_NEW_PER_SESSION)
    if not new:
        return None
    return db.word_from_row(rng.choice(new)), 0


def pick_learning_word(conn: sqlite3.Connection) -> tuple[Word, int] | None:
    """A started word that has not reached the mature review stage yet."""
    for row in db.fetch_due(conn, limit=STARTED_SCAN_LIMIT):
        stage = _stage_from_row(row)
        if stage < 3:
            return db.word_from_row(row), stage
    for row in db.fetch_started(conn, limit=STARTED_SCAN_LIMIT):
        stage = _stage_from_row(row)
        if stage < 3:
            return db.word_from_row(row), stage
    return None


def pick_review_word(conn: sqlite3.Connection) -> tuple[Word, int] | None:
    """A mature word whose scheduled review time has come."""
    for row in db.fetch_due(conn, limit=STARTED_SCAN_LIMIT):
        stage = _stage_from_row(row)
        if stage == 3:
            return db.word_from_row(row), stage
    return None


def create_task(
    conn: sqlite3.Connection,
    rng: random.Random | None = None,
    *,
    word_query: str | None = None,
    task_type: str | None = None,
    queue: str = "auto",
) -> dict | None:
    rng = rng or random.Random()

    if word_query:
        word = db.find_word(conn, word_query)
        if word is None:
            return None
        prog = db.get_progress(conn, word.db_id)
        stage = srs.stage(prog["reps"], prog["interval_days"]) if prog else 0
    else:
        pickers = {
            "auto": lambda: pick_word(conn, rng),
            "new": lambda: pick_new_word(conn, rng),
            "learning": lambda: pick_learning_word(conn),
            "review": lambda: pick_review_word(conn),
        }
        picked = pickers[queue]()
        if picked is None:
            return None
        word, stage = picked

    types = [task_type] if task_type else list(STAGE_TYPES[stage])
    for t in types + [t for t in FALLBACK if t not in types]:
        generator = GENERATORS.get(t)
        if generator is None:
            return None
        produced = generator.generate(word, conn, rng)
        if produced is None:
            if task_type:  # explicitly requested type is not applicable
                return None
            continue
        payload, expected = produced
        task_id = uuid.uuid4().hex[:12]
        db.save_task(conn, task_id, word.db_id, t, payload, expected)
        return {
            "task_id": task_id,
            "type": t,
            "word": word.lemma,
            "stage": stage,
            **payload,
        }
    return None


def submit_answer(conn: sqlite3.Connection, task_id: str, answer: str) -> dict | None:
    """Grade an answer, update SRS progress, return the verdict."""
    row = db.get_task(conn, task_id)
    if row is None:
        return None
    if row["answered_at"]:
        return {"error": "task already answered", "task_id": task_id}

    expected = json.loads(row["expected"])
    result = GENERATORS[row["type"]].check(expected, answer)

    word_id = row["word_id"]
    prog = db.get_progress(conn, word_id)
    schedule = srs.review(
        reps=prog["reps"] if prog else 0,
        lapses=prog["lapses"] if prog else 0,
        ease=prog["ease"] if prog else 2.5,
        interval_days=prog["interval_days"] if prog else 0.0,
        quality=result.quality,
    )
    db.upsert_progress(
        conn, word_id,
        reps=schedule.reps, lapses=schedule.lapses, ease=schedule.ease,
        interval_days=schedule.interval_days, due_at=schedule.due_at,
    )
    db.close_task(conn, task_id, result.correct)
    db.record_review(conn, word_id, task_id, row["type"], result.correct, result.quality)

    word = db.get_word(conn, word_id)
    return {
        "task_id": task_id,
        "correct": result.correct,
        "expected": result.expected,
        "note": result.note,
        "word": word.lemma if word else "",
        "translation": word.translation if word else "",
        "next_review_at": schedule.due_at,
        "interval_days": schedule.interval_days,
        "stage": srs.stage(schedule.reps, schedule.interval_days),
    }
