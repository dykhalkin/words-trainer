"""Shared answer-checking helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from ..models import Noun, PERSONS, TENSES, VerbPrep, Word


@dataclass(frozen=True)
class GeneratedExercise:
    payload: dict[str, Any]
    expected: dict[str, Any]
    response_mode: Literal["choice", "free_text"]
    answer_language: str | None
    grading_policy: Literal["deterministic", "tutor_on_mismatch"]

    def __iter__(self):
        """Keep tuple unpacking compatible for low-level generator callers."""
        yield self.payload
        yield self.expected


@dataclass
class CheckResult:
    correct: bool
    quality: int  # SM-2 quality 1..5
    expected: str  # canonical answer to show the user
    note: str = ""  # e.g. "опечатка", "пропущен артикль"


TENSE_LABELS = {
    "praesens": "Präsens",
    "perfekt": "Perfekt",
    "praeteritum": "Präteritum",
}


def short_verb_tense_strip(
    card: dict[str, Any], *, person_idx: int = 2
) -> dict[str, Any] | None:
    """Build a compact, learner-facing three-tense strip from a verb card."""
    if card.get("kind") != "verb" or not 0 <= person_idx < len(PERSONS):
        return None
    conjugation = card.get("conjugation")
    if not isinstance(conjugation, dict):
        return None
    person = PERSONS[person_idx]
    forms = []
    for tense in TENSES:
        cells = conjugation.get(tense)
        if not isinstance(cells, list) or len(cells) != len(PERSONS):
            return None
        cell = str(cells[person_idx]).strip()
        prefix = person + " "
        form = (
            cell[len(prefix) :].strip()
            if cell.casefold().startswith(prefix.casefold())
            else cell
        )
        if not form:
            return None
        forms.append({"tense": TENSE_LABELS[tense], "form": form})
    return {"person": person, "forms": forms}


def display_noun_lemma(article: str, singular: str, fallback: str) -> str:
    """Format a noun for learners, repairing legacy noun field layout."""
    article = article.strip().casefold()
    singular = re.sub(
        r"^(der|die|das)\s+", "", singular.strip(), flags=re.IGNORECASE
    )
    if article in {"der", "die", "das"} and singular:
        return f"{article} {singular}"
    return fallback


CASE_FORMS = {
    "Nom": ("Nom", "Nom.", "Nominativ"),
    "Akk": ("Akk", "Akk.", "Akkusativ"),
    "Dat": ("Dat", "Dat.", "Dativ"),
    "Gen": ("Gen", "Gen.", "Genitiv"),
}


def display_case(value: str) -> str:
    """Return the short learner-facing German case name."""
    raw = value.strip()
    key = raw.rstrip(".").casefold()
    for short, forms in CASE_FORMS.items():
        if key in {form.rstrip(".").casefold() for form in forms}:
            return short
    return raw.rstrip(".")


def case_variants(value: str) -> list[str]:
    """Return equivalent short and long spellings of a German case."""
    canonical = display_case(value)
    return list(CASE_FORMS.get(canonical, (canonical,)))


def reflexive_verb_variants(value: str) -> list[str]:
    """Treat infinitive-first and ``sich``-first reflexive labels as equivalent."""
    verb = re.sub(r"\s+", " ", value.strip())
    if verb.casefold().startswith("sich "):
        return [verb, f"{verb[5:]} sich"]
    if verb.casefold().endswith(" sich"):
        return [verb, f"sich {verb[:-5]}"]
    return [verb]


def display_rection(preposition: str, case: str) -> str:
    """Format verb government compactly, for example ``an Akk``."""
    prep = preposition.strip().rstrip("+").strip()
    return " ".join(part for part in (prep, display_case(case)) if part)


def display_verb_prep_lemma(
    verb: str, preposition: str, case: str, fallback: str
) -> str:
    """Format a verb-preposition card without the legacy plus sign."""
    compact = " ".join(
        part for part in (verb.strip(), display_rection(preposition, case)) if part
    )
    return compact or re.sub(r"\s+\+\s*", " ", fallback.strip())


def verb_prep_answer_variants(
    verb: str, preposition: str, case: str, *, include_verb: bool
) -> list[str]:
    """Build accepted compact/full-case and optional-plus rection spellings."""
    prep = preposition.strip().rstrip("+").strip()
    variants: list[str] = []
    for case_form in case_variants(case):
        rections = (f"{prep} {case_form}", f"{prep} + {case_form}")
        if include_verb:
            variants.extend(
                f"{verb_form} {rection}"
                for verb_form in reflexive_verb_variants(verb)
                for rection in rections
            )
        else:
            variants.extend(rections)
    return list(dict.fromkeys(variants))


def display_card_lemma(card: dict[str, Any], fallback: str) -> str:
    """Return a learner-visible lemma from structured card fields."""
    if card.get("kind") == "noun":
        return display_noun_lemma(
            str(card.get("article") or ""),
            str(card.get("singular") or ""),
            fallback,
        )
    if card.get("kind") == "verb_prep":
        return display_verb_prep_lemma(
            str(card.get("verb") or ""),
            str(card.get("preposition") or ""),
            str(card.get("case") or ""),
            fallback,
        )
    return fallback


def display_lemma(word: Word) -> str:
    """Return the learner-visible full or compact dictionary form."""
    if isinstance(word, Noun):
        return display_noun_lemma(word.article, word.singular, word.lemma)
    if isinstance(word, VerbPrep):
        return display_verb_prep_lemma(
            word.verb, word.preposition, word.case, word.lemma
        )
    return word.lemma


def normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[.!?,;:]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def fold_umlauts(text: str) -> str:
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    return text


def same(a: str, b: str) -> bool:
    a, b = normalize(a), normalize(b)
    return a == b or fold_umlauts(a) == fold_umlauts(b)


def check_text(
    answer: str, accepted: list[str], canonical: str,
    *, exact_quality: int = 4, allow_typo: bool = False,
) -> CheckResult:
    """Accept only normalized variants; semantic mismatches are graded by the tutor."""
    for variant in accepted:
        if same(answer, variant):
            return CheckResult(True, exact_quality, canonical)
    return CheckResult(False, 1, canonical)
