"""Loading words from CSV sources (semicolon-separated, no header).

Row kind is detected per line:
- 22 columns -> verb with full conjugation (4 base + 6 Praesens + 6 Perfekt + 6 Praeteritum)
- first column like "der/die/das X (die Xe)" -> noun
- first column like "denken an + Akk" -> verb with preposition
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .models import TENSES, Noun, Verb, VerbPrep, Word

NOUN_RE = re.compile(r"^(der|die|das)\s+(.+?)\s*(?:\((.+?)\))?$", re.IGNORECASE)
PREP_RE = re.compile(r"^(.+?)\s+\+\s*(\w+)$")


def parse_row(row: list[str], source_file: str) -> Word | None:
    row = [c.strip() for c in row]
    if not row or not row[0]:
        return None
    base = row[:4] + [""] * (4 - len(row))
    lemma, translation, example, pronunciation = base[0], base[1], base[2], base[3]
    common = dict(
        translation=translation,
        example=example,
        pronunciation=pronunciation,
        source_file=source_file,
    )

    if len(row) >= 22:
        conjugation = {
            tense: row[4 + i * 6 : 10 + i * 6] for i, tense in enumerate(TENSES)
        }
        return Verb(lemma=lemma, kind="verb", conjugation=conjugation, **common)

    m = PREP_RE.match(lemma)
    if m:
        head, case = m.groups()
        parts = head.split()
        return VerbPrep(
            lemma=lemma,
            kind="verb_prep",
            verb=" ".join(parts[:-1]),
            preposition=parts[-1],
            case=case,
            **common,
        )

    m = NOUN_RE.match(lemma)
    if m:
        article, singular, plural = m.groups()
        return Noun(
            lemma=f"{article} {singular}",
            kind="noun",
            article=article,
            singular=singular,
            plural_full=plural or "",
            **common,
        )

    return Word(lemma=lemma, kind="other", **common)


def load_file(path: Path) -> list[Word]:
    words: list[Word] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f, delimiter=";"):
            word = parse_row(row, path.name)
            if word:
                words.append(word)
    return words


def load_dir(data_dir: Path) -> list[Word]:
    """Load all *.csv under data_dir (recursively), deduplicated by lemma (first wins)."""
    seen: dict[str, Word] = {}
    for path in sorted(data_dir.rglob("*.csv")):
        for word in load_file(path):
            seen.setdefault(word.lemma, word)
    return list(seen.values())
