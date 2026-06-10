"""Word models parsed from CSV sources."""

from __future__ import annotations

from dataclasses import dataclass, field

PERSONS = ["ich", "du", "er/es/sie", "wir", "ihr", "Sie"]
TENSES = ["praesens", "perfekt", "praeteritum"]


@dataclass
class Word:
    lemma: str  # canonical key, e.g. "die Salbe", "bewegen", "denken an + Akk"
    kind: str  # noun | verb | verb_prep
    translation: str
    example: str
    pronunciation: str
    source_file: str = ""

    @property
    def headword(self) -> str:
        """The bare word to practice, without article/preposition decoration."""
        return self.lemma


@dataclass
class Noun(Word):
    article: str = ""  # der | die | das
    singular: str = ""  # Salbe
    plural_full: str = ""  # die Salben (may be empty)

    @property
    def headword(self) -> str:
        return self.singular

    @property
    def plural_noun(self) -> str:
        parts = self.plural_full.split()
        return parts[-1] if parts else ""


@dataclass
class Verb(Word):
    # conjugation[tense] = list of 6 full cells ("ich bewege", ... "Sie bewegen")
    conjugation: dict[str, list[str]] = field(default_factory=dict)

    @property
    def headword(self) -> str:
        return self.lemma

    def form(self, tense: str, person_idx: int) -> str:
        """Conjugated cell without the pronoun, e.g. 'habe bewegt'."""
        cell = self.conjugation[tense][person_idx]
        pronoun = PERSONS[person_idx]
        if cell.lower().startswith(pronoun.lower()):
            return cell[len(pronoun):].strip()
        return cell

    def all_form_tokens(self) -> set[str]:
        """Every conjugated verb token (last word of each cell), for cloze matching."""
        tokens: set[str] = set()
        for cells in self.conjugation.values():
            for cell in cells:
                parts = cell.split()
                if parts:
                    tokens.add(parts[-1])
        return tokens


@dataclass
class VerbPrep(Word):
    verb: str = ""  # denken / sich erinnern
    preposition: str = ""  # an
    case: str = ""  # Akk | Dat

    @property
    def headword(self) -> str:
        return self.verb

    @property
    def rection(self) -> str:
        return f"{self.preposition} + {self.case}"
