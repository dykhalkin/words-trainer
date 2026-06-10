"""SQLite persistence: word registry, SRS progress, pending tasks, review log."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import srs
from .models import Noun, Verb, VerbPrep, Word

WORD_CLASSES = {"noun": Noun, "verb": Verb, "verb_prep": VerbPrep, "other": Word}

SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    id          INTEGER PRIMARY KEY,
    lemma       TEXT UNIQUE NOT NULL,
    kind        TEXT NOT NULL,
    source_file TEXT,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    word_id       INTEGER PRIMARY KEY REFERENCES words(id),
    reps          INTEGER NOT NULL DEFAULT 0,
    lapses        INTEGER NOT NULL DEFAULT 0,
    ease          REAL    NOT NULL DEFAULT 2.5,
    interval_days REAL    NOT NULL DEFAULT 0,
    due_at        TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    word_id     INTEGER NOT NULL REFERENCES words(id),
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    expected    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    answered_at TEXT,
    correct     INTEGER
);
CREATE TABLE IF NOT EXISTS reviews (
    id        INTEGER PRIMARY KEY,
    word_id   INTEGER NOT NULL REFERENCES words(id),
    task_id   TEXT,
    task_type TEXT,
    correct   INTEGER NOT NULL,
    quality   INTEGER NOT NULL,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_progress_due ON progress(due_at);
CREATE INDEX IF NOT EXISTS idx_reviews_word ON reviews(word_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def word_to_json(word: Word) -> str:
    return json.dumps(dataclasses.asdict(word), ensure_ascii=False)


def word_from_row(row: sqlite3.Row) -> Word:
    data = json.loads(row["data"])
    cls = WORD_CLASSES.get(row["kind"], Word)
    word = cls(**data)
    word.db_id = row["id"]  # type: ignore[attr-defined]
    return word


def sync_words(conn: sqlite3.Connection, words: list[Word]) -> dict:
    """Upsert parsed words by lemma. Words removed from CSV are kept in DB."""
    added = updated = 0
    for w in words:
        data = word_to_json(w)
        row = conn.execute("SELECT id, data FROM words WHERE lemma = ?", (w.lemma,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO words (lemma, kind, source_file, data) VALUES (?, ?, ?, ?)",
                (w.lemma, w.kind, w.source_file, data),
            )
            added += 1
        elif row["data"] != data:
            conn.execute(
                "UPDATE words SET kind = ?, source_file = ?, data = ? WHERE id = ?",
                (w.kind, w.source_file, data, row["id"]),
            )
            updated += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    return {"added": added, "updated": updated, "total": total}


def get_word(conn: sqlite3.Connection, word_id: int) -> Word | None:
    row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
    return word_from_row(row) if row else None


def find_word(conn: sqlite3.Connection, query: str) -> Word | None:
    row = conn.execute(
        "SELECT * FROM words WHERE lemma = ? OR lemma LIKE ? ORDER BY length(lemma) LIMIT 1",
        (query, f"%{query}%"),
    ).fetchone()
    return word_from_row(row) if row else None


def sample_words(conn: sqlite3.Connection, kind: str, exclude_id: int, n: int) -> list[Word]:
    rows = conn.execute(
        "SELECT * FROM words WHERE kind = ? AND id != ? ORDER BY RANDOM() LIMIT ?",
        (kind, exclude_id, n),
    ).fetchall()
    return [word_from_row(r) for r in rows]


def get_progress(conn: sqlite3.Connection, word_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM progress WHERE word_id = ?", (word_id,)).fetchone()


def upsert_progress(
    conn: sqlite3.Connection,
    word_id: int,
    *,
    reps: int,
    lapses: int,
    ease: float,
    interval_days: float,
    due_at: str,
) -> None:
    conn.execute(
        """INSERT INTO progress (word_id, reps, lapses, ease, interval_days, due_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(word_id) DO UPDATE SET
             reps = excluded.reps, lapses = excluded.lapses, ease = excluded.ease,
             interval_days = excluded.interval_days, due_at = excluded.due_at,
             updated_at = excluded.updated_at""",
        (word_id, reps, lapses, ease, interval_days, due_at, now_iso()),
    )
    conn.commit()


def fetch_due(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Studied words whose review time has come, most overdue first."""
    return conn.execute(
        """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at
           FROM progress p JOIN words w ON w.id = p.word_id
           WHERE p.due_at <= ? ORDER BY p.due_at LIMIT ?""",
        (now_iso(), limit),
    ).fetchall()


def fetch_new(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Words never studied, in source order."""
    return conn.execute(
        """SELECT w.* FROM words w
           LEFT JOIN progress p ON p.word_id = w.id
           WHERE p.word_id IS NULL ORDER BY w.id LIMIT ?""",
        (limit,),
    ).fetchall()


def save_task(
    conn: sqlite3.Connection, task_id: str, word_id: int, task_type: str,
    payload: dict, expected: dict,
) -> None:
    conn.execute(
        "INSERT INTO tasks (id, word_id, type, payload, expected, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            task_id, word_id, task_type,
            json.dumps(payload, ensure_ascii=False),
            json.dumps(expected, ensure_ascii=False),
            now_iso(),
        ),
    )
    conn.commit()


def get_task(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def close_task(conn: sqlite3.Connection, task_id: str, correct: bool) -> None:
    conn.execute(
        "UPDATE tasks SET answered_at = ?, correct = ? WHERE id = ?",
        (now_iso(), int(correct), task_id),
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    studied = conn.execute("SELECT COUNT(*) FROM progress").fetchone()[0]
    due_now = conn.execute(
        "SELECT COUNT(*) FROM progress WHERE due_at <= ?", (now_iso(),)
    ).fetchone()[0]
    by_stage: dict[str, int] = {"0": 0, "1": 0, "2": 0, "3": 0}
    for row in conn.execute("SELECT reps, interval_days FROM progress"):
        by_stage[str(srs.stage(row["reps"], row["interval_days"]))] += 1
    week = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(correct), 0) AS ok
           FROM reviews WHERE ts >= datetime('now', '-7 days')"""
    ).fetchone()
    hardest = conn.execute(
        """SELECT w.lemma, w.data, p.lapses FROM progress p JOIN words w ON w.id = p.word_id
           WHERE p.lapses > 0 ORDER BY p.lapses DESC, p.ease ASC LIMIT 10"""
    ).fetchall()
    return {
        "words_total": total,
        "words_new": total - studied,
        "words_studied": studied,
        "due_now": due_now,
        "by_stage": by_stage,
        "reviews_last_7d": {
            "count": week["n"],
            "correct": week["ok"],
            "accuracy": round(week["ok"] / week["n"], 2) if week["n"] else None,
        },
        "hardest_words": [
            {
                "lemma": r["lemma"],
                "translation": json.loads(r["data"]).get("translation", ""),
                "lapses": r["lapses"],
            }
            for r in hardest
        ],
    }


def record_review(
    conn: sqlite3.Connection, word_id: int, task_id: str, task_type: str,
    correct: bool, quality: int,
) -> None:
    conn.execute(
        "INSERT INTO reviews (word_id, task_id, task_type, correct, quality, ts) VALUES (?, ?, ?, ?, ?, ?)",
        (word_id, task_id, task_type, int(correct), quality, now_iso()),
    )
    conn.commit()
