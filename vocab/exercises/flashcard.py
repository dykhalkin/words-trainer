"""Translation flashcards.

DeRu (stage 1): show German word, expect Russian translation (recognition).
RuDe (stage 2): show Russian, expect German word with article / preposition (recall).
"""

from __future__ import annotations

import random
import re
from typing import Any

from psycopg import AsyncConnection

from ..languages import ExerciseContext
from ..models import Noun, VerbPrep, Word
from .base import CheckResult, check_text, normalize, same


def translation_variants(translation: str) -> list[str]:
    """'думать о чем-то' -> itself; 'двигать, шевелить' -> both parts."""
    parts = re.split(r"[,/]", translation)
    variants = [translation] + [p for p in parts if p.strip()]
    return variants


class DeRu:
    @staticmethod
    async def generate(
        word: Word,
        conn: AsyncConnection[dict[str, Any]],
        rng: random.Random,
        context: ExerciseContext,
    ):
        payload = {"prompt": f"Переведи на русский: «{word.lemma}»"}
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
        hints = {
            "noun": " (с артиклем)",
            "verb_prep": " (с предлогом и падежом)",
        }
        payload = {
            "prompt": f"Скажи по-немецки: «{word.translation}»{hints.get(word.kind, '')}"
        }
        expected = {
            "lemma": word.lemma,
            "kind": word.kind,
            "headword": word.headword,
        }
        return payload, expected

    @staticmethod
    def check(expected: dict, answer: str) -> CheckResult:
        lemma, kind, headword = expected["lemma"], expected["kind"], expected["headword"]
        if kind == "noun":
            if same(answer, lemma):
                return CheckResult(True, 5, lemma)
            if same(answer, headword):
                return CheckResult(True, 3, lemma, note="слово верное, но нужен артикль")
            # wrong article, right noun
            m = re.match(r"^(der|die|das)\s+(.+)$", normalize(answer))
            if m and same(m.group(2), headword):
                return CheckResult(False, 2, lemma, note="неверный артикль")
            return check_text(answer, [lemma], lemma, exact_quality=5)
        if kind == "verb_prep":
            ans = normalize(answer).replace("+", " ").replace(".", " ")
            full = normalize(lemma).replace("+", " ")
            if " ".join(ans.split()) == " ".join(full.split()):
                return CheckResult(True, 5, lemma)
            if same(answer, headword):
                return CheckResult(True, 3, lemma, note="укажи также предлог и падеж")
            return check_text(answer, [lemma], lemma, exact_quality=5)
        return check_text(answer, [lemma], lemma, exact_quality=5)
