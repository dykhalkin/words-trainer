from __future__ import annotations

import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vocab import db, scheduler, srs, storage
from vocab.exercises import choice, flashcard
from vocab.models import Noun, Word


def make_word(lemma: str, translation: str, kind: str = "other") -> Word:
    return Word(
        lemma=lemma,
        kind=kind,
        translation=translation,
        example="",
        pronunciation="",
        source_file="test.csv",
    )


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


class ExerciseTests(unittest.TestCase):
    def test_rude_accepts_noun_without_article_as_low_quality(self) -> None:
        result = flashcard.RuDe.check(
            {"lemma": "die Salbe", "kind": "noun", "headword": "Salbe"},
            "Salbe",
        )
        self.assertTrue(result.correct)
        self.assertEqual(result.quality, 3)
        self.assertIn("артикль", result.note)

    def test_choice_falls_back_to_any_kind_for_tiny_decks(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmp:
            conn = db.connect(Path(tmp) / "progress.sqlite3")
            db.sync_words(
                conn,
                [
                    Noun(
                        lemma="die Salbe",
                        kind="noun",
                        translation="мазь",
                        example="",
                        pronunciation="",
                        source_file="test.csv",
                        article="die",
                        singular="Salbe",
                    ),
                    make_word("gehen", "идти"),
                    make_word("sofort", "сразу"),
                    make_word("krank sein", "болеть"),
                ],
            )
            word = db.find_word(conn, "Salbe")

            payload, expected = choice.generate(word, conn, random.Random(1))

        self.assertEqual(len(payload["options"]), 4)
        self.assertIn("мазь", payload["options"])
        self.assertEqual(expected["correct_text"], "мазь")


class SchedulerTests(unittest.TestCase):
    def test_task_answer_updates_progress_and_rejects_reuse(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmp:
            conn = db.connect(Path(tmp) / "progress.sqlite3")
            db.sync_words(
                conn,
                [
                    make_word("eins", "один"),
                    make_word("zwei", "два"),
                    make_word("drei", "три"),
                    make_word("vier", "четыре"),
                ],
            )

            task = scheduler.create_task(conn, rng=random.Random(1), word_query="eins")
            result = scheduler.submit_answer(conn, task["task_id"], "один")
            reused = scheduler.submit_answer(conn, task["task_id"], "один")
            word = db.find_word(conn, "eins")
            progress = db.get_progress(conn, word.db_id)

        self.assertTrue(result["correct"])
        self.assertEqual(result["stage"], 1)
        self.assertEqual(progress["reps"], 1)
        self.assertEqual(reused["error"], "task already answered")

    def test_task_queues_split_new_learning_and_review(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmp:
            conn = db.connect(Path(tmp) / "progress.sqlite3")
            db.sync_words(
                conn,
                [
                    make_word("neu", "новый"),
                    make_word("lernen", "учить"),
                    make_word("review", "повторять"),
                    make_word("extra", "лишний"),
                ],
            )
            now = datetime.now(timezone.utc)
            learning = db.find_word(conn, "lernen")
            review = db.find_word(conn, "review")
            db.upsert_progress(
                conn,
                learning.db_id,
                reps=1,
                lapses=0,
                ease=2.5,
                interval_days=1.0,
                due_at=(now + timedelta(days=1)).isoformat(timespec="seconds"),
            )
            db.upsert_progress(
                conn,
                review.db_id,
                reps=5,
                lapses=0,
                ease=2.5,
                interval_days=30.0,
                due_at=(now - timedelta(minutes=1)).isoformat(timespec="seconds"),
            )

            new_task = scheduler.create_task(conn, rng=random.Random(1), queue="new")
            learning_task = scheduler.create_task(conn, rng=random.Random(1), queue="learning")
            review_task = scheduler.create_task(conn, rng=random.Random(1), queue="review")

        self.assertIn(new_task["word"], {"neu", "extra"})
        self.assertEqual(new_task["stage"], 0)
        self.assertEqual(learning_task["word"], "lernen")
        self.assertEqual(learning_task["stage"], 1)
        self.assertEqual(review_task["word"], "review")
        self.assertEqual(review_task["stage"], 3)


if __name__ == "__main__":
    unittest.main()
