"""Language-specific identity, validation, and exercise capabilities."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import TENSES, Noun, Verb, VerbPrep, Word


class CardValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ExerciseContext:
    user_id: int
    language: str


@dataclass(frozen=True)
class LanguageSpec:
    code: str
    exercise_types: tuple[str, ...]


GERMAN = LanguageSpec(
    code="de",
    exercise_types=("choice", "flashcard_de_ru", "flashcard_ru_de", "cloze", "grammar"),
)
FALLBACK = LanguageSpec(
    code="*",
    exercise_types=("choice", "flashcard_de_ru", "flashcard_ru_de"),
)
REGISTRY = {"de": GERMAN}


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).strip())


def normalize_lemma(language: str, lemma: str) -> str:
    normalized = normalize_spaces(lemma)
    if language == "de":
        return normalized.lower()
    return normalized.casefold()


def normalize_deck_name(name: str) -> str:
    return normalize_spaces(name).casefold()


def language_spec(language: str) -> LanguageSpec:
    return REGISTRY.get(language, FALLBACK)


def validate_word(word: Word, language: str, *, strict_agent: bool = False) -> list[str]:
    """Validate a card, returning non-fatal warnings."""
    if not normalize_spaces(word.lemma):
        raise CardValidationError("lemma is required")
    if not normalize_spaces(word.translation):
        raise CardValidationError("translation is required")
    if strict_agent:
        if not normalize_spaces(word.example):
            raise CardValidationError("example is required")
        if not normalize_spaces(word.pronunciation):
            raise CardValidationError("pronunciation is required")

    if language == "de" and isinstance(word, Verb):
        if set(word.conjugation) != set(TENSES):
            raise CardValidationError("German verb requires Präsens, Perfekt, and Präteritum")
        for tense in TENSES:
            cells = word.conjugation.get(tense, [])
            if len(cells) != 6 or any(not normalize_spaces(cell) for cell in cells):
                raise CardValidationError(f"German verb requires six non-empty {tense} forms")
    if language == "de" and isinstance(word, Noun):
        if word.article.lower() not in {"der", "die", "das"}:
            raise CardValidationError("German noun requires der/die/das")
        if not normalize_spaces(word.singular):
            raise CardValidationError("German noun singular is required")
        if strict_agent and not normalize_spaces(word.plural_full):
            raise CardValidationError("German noun plural is required")
    if language == "de" and isinstance(word, VerbPrep):
        if not all(normalize_spaces(v) for v in (word.verb, word.preposition, word.case)):
            raise CardValidationError("German verb-preposition card requires verb, preposition, and case")

    warnings: list[str] = []
    if word.example and word.headword.lower() not in word.example.lower():
        warnings.append("example does not contain the literal headword; inflected form may still be valid")
    return warnings
