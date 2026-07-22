"""Cloze: the example sentence with the studied word blanked out (stages 2-3)."""

from __future__ import annotations

import random
import re
from typing import Any

from psycopg import AsyncConnection

from ..languages import ExerciseContext
from ..models import Noun, Verb, Word
from .base import CheckResult, GeneratedExercise, check_text, fold_umlauts

UMLAUT_MAP = [("au", "äu"), ("a", "ä"), ("o", "ö"), ("u", "ü")]
SEPARABLE_PREFIXES = (
    "vorbei", "zusammen", "herein", "heraus", "hinein", "hinaus",
    "vor", "an", "auf", "ab", "aus", "bei", "ein", "mit", "nach",
    "teil", "um", "weg", "zu", "statt", "fern", "fest", "los",
)


def candidate_forms(word: Word) -> set[str]:
    if isinstance(word, Noun):
        return {f for f in (word.singular, word.plural_noun) if f}
    if isinstance(word, Verb):
        return word.all_form_tokens() | {word.lemma}
    return set()


def _vowel_variants(stem: str) -> list[str]:
    """Inflection variants of a stem: umlaut (halt -> hält) and ablaut (bewerb -> bewirb)."""
    variants = []
    for plain, umlauted in UMLAUT_MAP:
        if plain in stem:
            variants.append(stem.replace(plain, umlauted, 1))
            break
    last_ei = stem.rfind("ei", 1)
    if last_ei > 0:  # entscheid -> entschied, schreib -> schrieb
        variants.append(stem[:last_ei] + "ie" + stem[last_ei + 2 :])
    last_e = stem.rfind("e", 1)
    if last_e > 0 and stem[last_e : last_e + 2] != "ei":  # geb -> gib, seh -> sieh
        variants.append(stem[:last_e] + "i" + stem[last_e + 1 :])
        variants.append(stem[:last_e] + "ie" + stem[last_e + 1 :])
    return variants


def stems(word: Word) -> list[str]:
    """Verb stem variants: plain, umlaut/ablaut, separable prefix stripped."""
    head = word.headword.split()[-1]
    stem = re.sub(r"(en|n)$", "", head)
    if len(stem) < 3:
        return []
    bases = [stem]
    for prefix in SEPARABLE_PREFIXES:
        if stem.startswith(prefix) and len(stem) - len(prefix) >= 3:
            bases.append(stem[len(prefix):])
            break
    variants: list[str] = []
    for base in bases:
        variants.append(base)
        variants.extend(_vowel_variants(base))
    return variants


def find_blank(word: Word) -> str | None:
    """The token of the example sentence to blank out, or None."""
    tokens = re.findall(r"[A-Za-zÄÖÜäöüß]+", word.example)
    forms = {f.lower() for f in candidate_forms(word)}
    for tok in tokens:
        if tok.lower() in forms:
            return tok
    for stem in stems(word):
        s = stem.lower()
        for tok in tokens:
            t = tok.lower()
            if t.startswith(s) or t.startswith("ge" + s):  # bereite / gehört
                return tok
    return None


async def generate(
    word: Word,
    conn: AsyncConnection[dict[str, Any]],
    rng: random.Random,
    context: ExerciseContext,
):
    if not word.example:
        return None
    target = find_blank(word)
    if not target:
        return None
    gap = "_" * max(4, len(target))
    sentence = re.sub(rf"\b{re.escape(target)}\b", gap, word.example, count=1)
    payload = {
        "prompt": f"Вставь пропущенное слово: {sentence}",
        "hint": f"{word.translation} ({word.lemma})",
    }
    expected = {"answer": target, "sentence": word.example}
    return GeneratedExercise(
        payload, expected, "free_text", context.language, "tutor_on_mismatch"
    )


def check(expected: dict, answer: str) -> CheckResult:
    result = check_text(answer.strip(), [expected["answer"]], expected["answer"], exact_quality=5)
    result.note = (result.note + " " if result.note else "") + f"Предложение: {expected['sentence']}"
    return result
