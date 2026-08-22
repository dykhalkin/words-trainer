from __future__ import annotations

import unittest
from datetime import datetime, timezone

from vocab import srs, storage
from vocab.exercises import choice_ru_de, cloze, flashcard
from vocab.models import Noun


class StorageTests(unittest.TestCase):
    def test_parse_supported_row_kinds(self) -> None:
        noun = storage.parse_row(
            ["die Salbe (die Salben)", "мазь", "Ich trage die Salbe auf.", "zal-beh"],
            "nouns.csv",
        )
        self.assertIsInstance(noun, Noun)
        self.assertEqual(noun.lemma, "die Salbe")
        self.assertEqual(noun.plural_full, "die Salben")

        verb = storage.parse_row(
            ["halten", "держать", "Er hält die Tür offen.", "hal-ten"]
            + [f"form {i}" for i in range(18)],
            "verbs.csv",
        )
        self.assertEqual(verb.kind, "verb")
        self.assertEqual(len(verb.conjugation["praesens"]), 6)

        prep = storage.parse_row(
            ["sich erinnern an + Akk", "вспоминать о чем-то", "", ""],
            "prep.csv",
        )
        self.assertEqual(prep.kind, "verb_prep")
        self.assertEqual(prep.verb, "sich erinnern")
        self.assertEqual(prep.preposition, "an")
        self.assertEqual(prep.case, "Akk")


class SrsTests(unittest.TestCase):
    def test_success_and_lapse_schedule(self) -> None:
        now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        first = srs.review(
            reps=0, lapses=0, ease=2.5, interval_days=0.0, quality=4, now=now
        )
        self.assertEqual(first.reps, 1)
        self.assertEqual(first.interval_days, 1.0)
        self.assertEqual(srs.stage(first.reps, first.interval_days), 1)

        lapse = srs.review(
            reps=3, lapses=0, ease=2.5, interval_days=7.0, quality=1, now=now
        )
        self.assertEqual(lapse.reps, 0)
        self.assertEqual(lapse.lapses, 1)
        self.assertEqual(lapse.interval_days, 0.0)
        self.assertEqual(srs.stage(lapse.reps, lapse.interval_days), 0)

    def test_stage_boundaries_match_the_learning_guide(self) -> None:
        self.assertEqual(srs.stage(0, 0.0), 0)
        self.assertEqual(srs.stage(1, 1.0), 1)
        self.assertEqual(srs.stage(2, 3.0), 1)
        self.assertEqual(srs.stage(3, 8.0), 2)
        self.assertEqual(srs.stage(4, 20.9), 2)
        self.assertEqual(srs.stage(4, 21.0), 3)
        self.assertEqual(srs.stage(5, 10.0), 3)


class ExerciseTests(unittest.TestCase):
    def test_reverse_choice_checks_the_studied_language_answer(self) -> None:
        result = choice_ru_de.check(
            {"correct_index": 1, "correct_text": "die Salbe"}, "die Salbe"
        )
        self.assertTrue(result.correct)
        self.assertEqual(result.expected, "die Salbe")

    def test_rude_mismatch_requires_semantic_grading(self) -> None:
        result = flashcard.RuDe.check(
            {"lemma": "die Salbe", "kind": "noun", "headword": "Salbe"},
            "Salbe",
        )
        self.assertFalse(result.correct)
        self.assertEqual(result.quality, 1)

    def test_cloze_does_not_duplicate_the_example_in_note(self) -> None:
        result = cloze.check(
            {"answer": "gehe", "sentence": "Ich gehe nach Hause."}, "gehe"
        )
        self.assertTrue(result.correct)
        self.assertEqual(result.note, "")


if __name__ == "__main__":
    unittest.main()
