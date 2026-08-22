"""Target-language recall flashcards plus the legacy native-answer checker.

Only ``RuDe`` is registered for new tasks: the prompt may contain a native-language
translation, but the learner always types the target-language lemma. ``DeRu`` remains
solely so historical task data can still be interpreted during the migration window.
"""

from __future__ import annotations

import random
import re
from typing import Any

from psycopg import AsyncConnection

from ..languages import ExerciseContext, language_spec
from ..models import Noun, Phrase, Verb, VerbPrep, Word
from .base import (
    CheckResult,
    GeneratedExercise,
    check_text,
    display_lemma,
    reflexive_verb_variants,
    verb_prep_answer_variants,
)


def translation_variants(translation: str) -> list[str]:
    """'думать о чем-то' -> itself; 'двигать, шевелить' -> both parts."""
    parts = re.split(r"[,/]", translation)
    variants = [translation] + [p for p in parts if p.strip()]
    return variants


class DeRu:
    """Legacy, unregistered checker for historical ``flashcard_de_ru`` rows."""

    @staticmethod
    async def generate(
        word: Word,
        conn: AsyncConnection[dict[str, Any]],
        rng: random.Random,
        context: ExerciseContext,
    ):
        payload = {"prompt": f"Переведи на русский: «{display_lemma(word)}»"}
        expected = {"translation": word.translation}
        return payload, expected

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        translation = expected["translation"]
        return check_text(answer, translation_variants(translation), translation)


class RuDe:
    @staticmethod
    async def generate(
        word: Word,
        conn: AsyncConnection[dict[str, Any]],
        rng: random.Random,
        context: ExerciseContext,
    ):
        visible_word = display_lemma(word)
        hints = {
            "noun": " (с артиклем)",
            "verb_prep": " (с предлогом и падежом)",
            "phrase": " (целиком фразой)",
        }
        payload = {
            "prompt": (
                f"Скажи {language_spec(context.language).prompt_name_ru}: "
                f"«{word.translation}»{hints.get(word.kind, '')}"
            )
        }
        expected = {
            "lemma": visible_word,
            "kind": word.kind,
            "headword": visible_word if isinstance(word, Noun) else word.headword,
        }
        if isinstance(word, Verb):
            expected["reflexive"] = word.lemma.casefold().startswith("sich ")
        elif isinstance(word, VerbPrep):
            expected.update(
                verb=word.verb,
                preposition=word.preposition,
                case=word.case,
            )
        elif isinstance(word, Phrase):
            expected["phrase_type"] = word.phrase_type
        return GeneratedExercise(
            payload, expected, "free_text", context.language, "tutor_on_mismatch"
        )

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        lemma = expected["lemma"]
        accepted = [lemma]
        if expected.get("kind") == "verb" and (
            expected.get("reflexive") or lemma.casefold().startswith("sich ")
        ):
            accepted = reflexive_verb_variants(lemma)
        elif expected.get("kind") == "verb_prep":
            if expected.get("preposition") and expected.get("case"):
                accepted += verb_prep_answer_variants(
                    str(expected.get("verb") or expected.get("headword") or ""),
                    str(expected["preposition"]),
                    str(expected["case"]),
                    include_verb=True,
                )
        return check_text(answer, accepted, lemma, exact_quality=5)
