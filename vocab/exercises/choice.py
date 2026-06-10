"""Multiple choice: pick the translation of a German word (easiest, stage 0)."""

from __future__ import annotations

import random
import sqlite3

from .. import db
from ..models import Word
from .base import CheckResult

N_OPTIONS = 4


def generate(word: Word, conn: sqlite3.Connection, rng: random.Random):
    word_id = getattr(word, "db_id", 0)
    distractors = db.sample_words(conn, word.kind, word_id, N_OPTIONS - 1)
    if len(distractors) < N_OPTIONS - 1:  # tiny deck: take any kind
        distractors = db.sample_words(conn, word.kind, word_id, 0)[: N_OPTIONS - 1]
    options = [w.translation for w in distractors] + [word.translation]
    rng.shuffle(options)
    correct_idx = options.index(word.translation)
    payload = {
        "prompt": f"Что означает «{word.lemma}»?",
        "options": options,
    }
    expected = {"correct_index": correct_idx, "correct_text": word.translation}
    return payload, expected


def check(expected: dict, answer: str) -> CheckResult:
    answer = answer.strip()
    correct_idx = expected["correct_index"]
    correct_text = expected["correct_text"]
    if answer.isdigit():
        chosen = int(answer) - 1
        ok = chosen == correct_idx
    else:
        ok = answer.casefold().strip() == correct_text.casefold().strip()
    return CheckResult(correct=ok, quality=4 if ok else 1, expected=correct_text)
