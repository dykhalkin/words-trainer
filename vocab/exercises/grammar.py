"""Grammar drills (stage 3): noun articles & plural, verb conjugation, verb rection."""

from __future__ import annotations

import random
from typing import Any

from psycopg import AsyncConnection

from .. import db
from ..languages import ExerciseContext
from ..models import PERSONS, Noun, Verb, VerbPrep, Word
from .base import CheckResult, GeneratedExercise, check_text

TENSE_NAMES = {"praesens": "Präsens", "perfekt": "Perfekt", "praeteritum": "Präteritum"}


async def generate(
    word: Word,
    conn: AsyncConnection[dict[str, Any]],
    rng: random.Random,
    context: ExerciseContext,
):
    produced = None
    if isinstance(word, Noun):
        produced = _noun_task(word, rng)
    elif isinstance(word, Verb):
        produced = _verb_task(word, rng)
    elif isinstance(word, VerbPrep):
        produced = await _prep_task(word, conn, rng, context)
    if produced is None:
        return None
    payload, expected = produced
    if payload.get("options"):
        return GeneratedExercise(payload, expected, "choice", None, "deterministic")
    return GeneratedExercise(
        payload, expected, "free_text", context.language, "tutor_on_mismatch"
    )


def _noun_task(word: Noun, rng: random.Random):
    subtypes = ["article"] + (["plural"] if word.plural_noun else [])
    subtype = rng.choice(subtypes)
    if subtype == "article":
        payload = {
            "prompt": f"Какой артикль у слова «{word.singular}» ({word.translation})?",
            "options": ["der", "die", "das"],
        }
        expected = {
            "sub": "article",
            "answer": word.article,
            "accepted": [word.article],
            "options": payload["options"],
        }
    else:
        payload = {
            "prompt": f"Wie lautet der Plural von «{word.lemma}» ({word.translation})?",
        }
        expected = {
            "sub": "plural",
            "answer": word.plural_full,
            "accepted": [word.plural_full, word.plural_noun],
        }
    return payload, expected


def _verb_task(word: Verb, rng: random.Random):
    if set(word.conjugation) != set(TENSE_NAMES):
        return None
    if any(len(word.conjugation.get(tense, [])) != len(PERSONS) for tense in TENSE_NAMES):
        return None
    tense = rng.choice(list(word.conjugation))
    person_idx = rng.randrange(len(PERSONS))
    form = word.form(tense, person_idx)
    full_cell = word.conjugation[tense][person_idx]
    payload = {
        "prompt": (
            f"«{word.lemma}» ({word.translation}): "
            f"{TENSE_NAMES.get(tense, tense)}, {PERSONS[person_idx]} — ?"
        ),
    }
    expected = {"sub": "conjugation", "answer": full_cell, "accepted": [form, full_cell]}
    return payload, expected


async def _prep_task(
    word: VerbPrep,
    conn: AsyncConnection[dict[str, Any]],
    rng: random.Random,
    context: ExerciseContext,
):
    word_id = getattr(word, "db_id", 0)
    others = await db.sample_words(
        conn,
        user_id=context.user_id,
        language=context.language,
        kind="verb_prep",
        exclude_id=word_id,
        limit=12,
    )
    pool = {w.rection for w in others if isinstance(w, VerbPrep)} - {word.rection}
    options = rng.sample(sorted(pool), k=min(3, len(pool))) + [word.rection]
    rng.shuffle(options)
    payload = {
        "prompt": f"«{word.verb}» ({word.translation}): какой предлог и падеж?",
        "options": options,
    }
    expected = {
        "sub": "rection",
        "answer": word.rection,
        "accepted": [word.rection, f"{word.preposition} {word.case}", word.preposition + word.case],
        "options": options,
    }
    return payload, expected


def check(expected: dict, answer: str) -> CheckResult:
    answer = answer.strip()
    options = expected.get("options")
    if answer.isdigit() and options and 1 <= int(answer) <= len(options):
        answer = options[int(answer) - 1]
    exact_quality = 4 if expected["sub"] in ("article", "rection") else 5
    return check_text(
        answer, expected["accepted"], expected["answer"],
        exact_quality=exact_quality, allow_typo=False,
    )
