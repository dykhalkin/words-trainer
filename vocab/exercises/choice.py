"""Multiple choice: pick the translation of a German word (easiest, stage 0)."""

from __future__ import annotations

import random
from typing import Any

from psycopg import AsyncConnection

from .. import db
from ..languages import ExerciseContext
from ..models import Word
from .base import CheckResult, GeneratedExercise

N_OPTIONS = 4


async def generate(
    word: Word,
    conn: AsyncConnection[dict[str, Any]],
    rng: random.Random,
    context: ExerciseContext,
):
    word_id = getattr(word, "db_id", 0)
    distractors = await db.sample_words(
        conn,
        user_id=context.user_id,
        language=context.language,
        kind=word.kind,
        exclude_id=word_id,
        limit=N_OPTIONS - 1,
    )
    if len(distractors) < N_OPTIONS - 1:  # tiny deck: take any kind
        seen = {w.lemma for w in distractors}
        candidates = await db.sample_words(
            conn,
            user_id=context.user_id,
            language=context.language,
            exclude_id=word_id,
            limit=N_OPTIONS * 2,
        )
        for candidate in candidates:
            if candidate.lemma not in seen:
                distractors.append(candidate)
                seen.add(candidate.lemma)
            if len(distractors) == N_OPTIONS - 1:
                break
    options = [w.translation for w in distractors] + [word.translation]
    rng.shuffle(options)
    correct_idx = options.index(word.translation)
    payload = {
        "prompt": f"Что означает «{word.lemma}»?",
        "options": options,
    }
    expected = {"correct_index": correct_idx, "correct_text": word.translation}
    return GeneratedExercise(payload, expected, "choice", None, "deterministic")


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
