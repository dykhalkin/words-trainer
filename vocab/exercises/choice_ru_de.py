"""Multiple choice: pick the studied-language word for a Russian translation."""

from __future__ import annotations

import random
from typing import Any

from psycopg import AsyncConnection

from .. import db
from ..languages import ExerciseContext, language_spec
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
    if len(distractors) < N_OPTIONS - 1:
        seen = {candidate.lemma for candidate in distractors}
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
    options = [candidate.lemma for candidate in distractors] + [word.lemma]
    rng.shuffle(options)
    correct_idx = options.index(word.lemma)
    prompt_name = language_spec(context.language).prompt_name_ru
    payload = {
        "prompt": f"Как {prompt_name} «{word.translation}»?",
        "options": options,
    }
    expected = {"correct_index": correct_idx, "correct_text": word.lemma}
    return GeneratedExercise(payload, expected, "choice", None, "deterministic")


def check(expected: dict, answer: str) -> CheckResult:
    answer = answer.strip()
    correct_idx = expected["correct_index"]
    correct_text = expected["correct_text"]
    if answer.isdigit():
        chosen = int(answer) - 1
        correct = chosen == correct_idx
    else:
        correct = answer.casefold().strip() == correct_text.casefold().strip()
    return CheckResult(
        correct=correct,
        quality=4 if correct else 1,
        expected=correct_text,
    )
