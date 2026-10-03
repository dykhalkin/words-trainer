"""Cloze: the example sentence with the studied word blanked out (stages 2-3)."""

from __future__ import annotations

import random
import re
from typing import Any

from psycopg import AsyncConnection

from ..languages import ExerciseContext
from ..models import Noun, Phrase, Verb, Word
from .base import CheckResult, GeneratedExercise, check_text

UMLAUT_MAP = [("au", "äu"), ("a", "ä"), ("o", "ö"), ("u", "ü")]
SEPARABLE_PREFIXES = (
    "vorbei", "zusammen", "herein", "heraus", "hinein", "hinaus",
    "vor", "an", "auf", "ab", "aus", "bei", "ein", "mit", "nach",
    "teil", "um", "weg", "zu", "statt", "fern", "fest", "los",
)


REFLEXIVE_PRONOUNS = {"mich", "dich", "sich", "uns", "euch"}
AUXILIARY_FORMS = {
    "bin",
    "bist",
    "ist",
    "sind",
    "seid",
    "war",
    "warst",
    "waren",
    "wart",
    "habe",
    "hast",
    "hat",
    "haben",
    "habt",
    "hatte",
    "hattest",
    "hatten",
    "hattet",
    "werde",
    "wirst",
    "wird",
    "werden",
    "werdet",
    "wurde",
    "wurdest",
    "wurden",
    "wurdet",
}


def candidate_forms(word: Word) -> set[str]:
    """Lexical verb forms, excluding auxiliaries, pronouns, and particles.

    A conjugation cell can end in a separable prefix (``ich rufe an``) or a
    reflexive pronoun (``ich freue mich``), so taking its final token produces
    gaps unrelated to the card's learning target.  In a simple form the
    lexical verb is the first token after the subject; with an auxiliary it is
    the final content token (the participle or infinitive).
    """
    if not isinstance(word, Verb):
        return set()
    forms = {word.lemma}
    for tense, cells in word.conjugation.items():
        for person_idx, _cell in enumerate(cells):
            form = word.form(tense, person_idx)
            tokens = re.findall(r"[A-Za-zÄÖÜäöüß]+", form)
            content = [
                token for token in tokens if token.casefold() not in REFLEXIVE_PRONOUNS
            ]
            if not content:
                continue
            target = (
                content[-1] if content[0].casefold() in AUXILIARY_FORMS else content[0]
            )
            forms.add(target)
    return forms


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
    # A noun cloze necessarily isolates an inflected bare noun or article. Both
    # violate the full-form invariant, so nouns use recall and grammar instead.
    if isinstance(word, (Noun, Phrase)):
        return None
    if not word.example:
        return None
    target = find_blank(word)
    if not target:
        return None
    gap = "_" * max(4, len(target))
    sentence = re.sub(rf"\b{re.escape(target)}\b", gap, word.example, count=1)
    payload = {
        "prompt": f"Вставь пропущенное слово: {sentence}",
        "hint": word.translation,
    }
    expected = {"answer": target, "sentence": word.example}
    return GeneratedExercise(
        payload, expected, "free_text", context.language, "tutor_on_mismatch"
    )


def check(expected: dict, answer: str) -> CheckResult:
    return check_text(
        answer.strip(), [expected["answer"]], expected["answer"], exact_quality=5
    )
