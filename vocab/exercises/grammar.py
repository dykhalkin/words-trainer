"""Grammar drills (stage 3): noun articles & plural, verb conjugation, verb rection."""

from __future__ import annotations

import random
import sqlite3

from .. import db
from ..models import PERSONS, Noun, Verb, VerbPrep, Word
from .base import CheckResult, check_text

TENSE_NAMES = {"praesens": "Präsens", "perfekt": "Perfekt", "praeteritum": "Präteritum"}


def generate(word: Word, conn: sqlite3.Connection, rng: random.Random):
    if isinstance(word, Noun):
        return _noun_task(word, rng)
    if isinstance(word, Verb):
        return _verb_task(word, rng)
    if isinstance(word, VerbPrep):
        return _prep_task(word, conn, rng)
    return None


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


def _prep_task(word: VerbPrep, conn: sqlite3.Connection, rng: random.Random):
    word_id = getattr(word, "db_id", 0)
    others = db.sample_words(conn, "verb_prep", word_id, 12)
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
