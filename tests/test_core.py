from __future__ import annotations

import asyncio
import random
import unittest
from datetime import datetime, timezone

from vocab import scheduler, srs, storage
from vocab.exercises import choice_ru_de, cloze, flashcard, grammar, phrase
from vocab.exercises.base import (
    display_lemma,
    short_verb_tense_strip,
    verb_prep_answer_variants,
)
from vocab.languages import ExerciseContext
from vocab.models import Noun, Phrase, Verb, VerbPrep


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

        phrase_card = storage.parse_row(
            [
                "auf jeden Fall",
                "в любом случае",
                "Ich komme auf jeden Fall.",
                "ауф йеден фаль",
                "phrase:fixed",
            ],
            "phrases.csv",
        )
        self.assertIsInstance(phrase_card, Phrase)
        self.assertEqual(phrase_card.phrase_type, "fixed")


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
    def test_cloze_targets_verb_instead_of_reflexive_pronoun(self) -> None:
        verb = Verb(
            lemma="sich freuen",
            kind="verb",
            translation="радоваться",
            example="Ich freue mich auf das Wochenende.",
            pronunciation="фройен",
            conjugation={
                "praesens": [
                    "ich freue mich",
                    "du freust dich",
                    "er/es/sie freut sich",
                    "wir freuen uns",
                    "ihr freut euch",
                    "Sie freuen sich",
                ],
                "perfekt": [
                    "ich habe mich gefreut",
                    "du hast dich gefreut",
                    "er/es/sie hat sich gefreut",
                    "wir haben uns gefreut",
                    "ihr habt euch gefreut",
                    "Sie haben sich gefreut",
                ],
                "praeteritum": [
                    "ich freute mich",
                    "du freutest dich",
                    "er/es/sie freute sich",
                    "wir freuten uns",
                    "ihr freutet euch",
                    "Sie freuten sich",
                ],
            },
        )

        self.assertEqual(cloze.find_blank(verb), "freue")
        self.assertTrue(
            {"mich", "dich", "sich", "uns", "euch"}.isdisjoint(
                cloze.candidate_forms(verb)
            )
        )

    def test_cloze_targets_lexical_part_of_separable_verb(self) -> None:
        verb = self._verb()

        self.assertEqual(cloze.find_blank(verb), "baut")
        self.assertNotIn("auf", cloze.candidate_forms(verb))

        exercise = asyncio.run(
            cloze.generate(
                verb,
                None,  # type: ignore[arg-type]
                random.Random(1),
                ExerciseContext(user_id=1, language="de"),
            )
        )
        self.assertIsNotNone(exercise)
        self.assertEqual(exercise.payload["hint"], "строить")
        self.assertNotIn(verb.lemma, exercise.payload["hint"])

    @staticmethod
    def _verb() -> Verb:
        return Verb(
            lemma="aufbauen",
            kind="verb",
            translation="строить",
            example="Sie baut ein Haus auf.",
            pronunciation="ауфбауэн",
            conjugation={
                "praesens": [
                    "ich baue auf", "du baust auf", "er/es/sie baut auf",
                    "wir bauen auf", "ihr baut auf", "Sie bauen auf",
                ],
                "perfekt": [
                    "ich habe aufgebaut", "du hast aufgebaut",
                    "er/es/sie hat aufgebaut", "wir haben aufgebaut",
                    "ihr habt aufgebaut", "Sie haben aufgebaut",
                ],
                "praeteritum": [
                    "ich baute auf", "du bautest auf", "er/es/sie baute auf",
                    "wir bauten auf", "ihr bautet auf", "Sie bauten auf",
                ],
            },
        )

    def test_verb_tense_strip_uses_compact_third_person_forms(self) -> None:
        verb = self._verb()
        strip = short_verb_tense_strip(
            {"kind": verb.kind, "conjugation": verb.conjugation}
        )

        self.assertEqual(
            strip,
            {
                "person": "er/es/sie",
                "forms": [
                    {"tense": "Präsens", "form": "baut auf"},
                    {"tense": "Perfekt", "form": "hat aufgebaut"},
                    {"tense": "Präteritum", "form": "baute auf"},
                ],
            },
        )

    def test_verb_tense_drills_start_at_consolidating_stage(self) -> None:
        verb = self._verb()

        self.assertNotIn(
            "grammar", scheduler._stage_task_types(verb, 0, random.Random(1))
        )
        self.assertNotIn(
            "grammar", scheduler._stage_task_types(verb, 1, random.Random(1))
        )
        self.assertIn(
            "grammar", scheduler._stage_task_types(verb, 2, random.Random(1))
        )
        self.assertIn(
            "grammar", scheduler._stage_task_types(verb, 3, random.Random(1))
        )

    def test_reverse_choice_checks_the_studied_language_answer(self) -> None:
        result = choice_ru_de.check(
            {"correct_index": 1, "correct_text": "die Salbe"}, "die Salbe"
        )
        self.assertTrue(result.correct)
        self.assertEqual(result.expected, "die Salbe")

    def test_rude_mismatch_requires_semantic_grading(self) -> None:
        result = flashcard.RuDe.check(
            {"lemma": "die Salbe", "kind": "noun", "headword": "die Salbe"},
            "Salbe",
        )
        self.assertFalse(result.correct)
        self.assertEqual(result.quality, 1)

    def test_reflexive_infinitive_order_is_not_an_error(self) -> None:
        expected = {
            "lemma": "sich verstehen",
            "kind": "verb",
            "headword": "sich verstehen",
            "reflexive": True,
        }
        self.assertTrue(flashcard.RuDe.check(expected, "verstehen sich").correct)
        self.assertTrue(flashcard.RuDe.check(expected, "sich verstehen").correct)
        self.assertFalse(flashcard.RuDe.check(expected, "verstehen").correct)

    def test_verb_preposition_card_is_compact_and_accepts_case_variants(self) -> None:
        word = VerbPrep(
            lemma="denken an + Akk",
            kind="verb_prep",
            translation="думать о",
            example="Ich denke an dich.",
            pronunciation="denken",
            verb="denken",
            preposition="an",
            case="Akkusativ",
        )
        self.assertEqual(display_lemma(word), "denken an Akk")
        expected = {
            "lemma": "denken an Akk",
            "kind": "verb_prep",
            "headword": "denken",
            "verb": "denken",
            "preposition": "an",
            "case": "Akk",
        }
        for answer in (
            "denken an Akk",
            "denken an + Akk",
            "denken an Akk.",
            "denken an Akkusativ",
        ):
            with self.subTest(answer=answer):
                self.assertTrue(flashcard.RuDe.check(expected, answer).correct)

        rection_expected = {
            "sub": "rection",
            "answer": "an Akk",
            "accepted": verb_prep_answer_variants(
                "denken", "an", "Akk", include_verb=False
            )
            + verb_prep_answer_variants(
                "denken", "an", "Akk", include_verb=True
            ),
        }
        self.assertTrue(grammar.check(rection_expected, "an Akkusativ").correct)
        self.assertTrue(grammar.check(rection_expected, "denken an Akk").correct)

    def test_reflexive_verb_preposition_accepts_sich_after_infinitive(self) -> None:
        expected = {
            "lemma": "sich erinnern an Akk",
            "kind": "verb_prep",
            "headword": "sich erinnern",
            "verb": "sich erinnern",
            "preposition": "an",
            "case": "Akk",
        }
        self.assertTrue(
            flashcard.RuDe.check(expected, "erinnern sich an Akkusativ").correct
        )
        self.assertFalse(flashcard.RuDe.check(expected, "erinnern an Akk").correct)

    def test_phrase_cloze_uses_the_phrase_as_one_learning_unit(self) -> None:
        self.assertEqual(
            phrase.phrase_gap(
                "eine Entscheidung treffen",
                "Wir müssen eine Entscheidung treffen.",
            ),
            ("Wir müssen _________________________.", "eine Entscheidung treffen"),
        )
        self.assertIsNone(
            phrase.phrase_gap(
                "es kommt auf … an",
                "Es kommt auf das Wetter an.",
            )
        )
        expected = {
            "answer": "eine Entscheidung treffen",
            "phrase": "eine Entscheidung treffen",
            "accepted": ["eine Entscheidung treffen"],
        }
        self.assertFalse(phrase.Cloze.check(expected, "treffen").correct)
        self.assertTrue(
            phrase.Cloze.check(expected, "eine Entscheidung treffen").correct
        )

    def test_phrase_context_accepts_only_exact_local_variants_before_lm(self) -> None:
        expected = {
            "answer": "auf jeden Fall",
            "accepted": ["auf jeden Fall", "Ich komme auf jeden Fall."],
        }
        self.assertTrue(phrase.Context.check(expected, "auf jeden Fall").correct)
        self.assertTrue(
            phrase.Context.check(expected, "Ich komme auf jeden Fall!").correct
        )
        self.assertFalse(phrase.Context.check(expected, "jedenfalls").correct)

    def test_noun_grammar_requires_the_article_in_every_answer(self) -> None:
        noun = Noun(
            lemma="die Salbe",
            kind="noun",
            translation="мазь",
            example="Die Salbe hilft.",
            pronunciation="zal-be",
            article="die",
            singular="Salbe",
            plural_full="die Salben",
        )

        class FixedChoice:
            def __init__(self, value: str) -> None:
                self.value = value

            def choice(self, values):
                self.assert_value(values)
                return self.value

            def assert_value(self, values) -> None:
                if self.value not in values:
                    raise AssertionError(f"{self.value} not in {values}")

        article_payload, article_expected = grammar._noun_task(  # type: ignore[attr-defined]
            noun, FixedChoice("article")  # type: ignore[arg-type]
        )
        self.assertEqual(
            article_payload["options"], ["der Salbe", "die Salbe", "das Salbe"]
        )
        self.assertFalse(grammar.check(article_expected, "die").correct)
        self.assertTrue(grammar.check(article_expected, "die Salbe").correct)

        _, plural_expected = grammar._noun_task(  # type: ignore[attr-defined]
            noun, FixedChoice("plural")  # type: ignore[arg-type]
        )
        self.assertFalse(grammar.check(plural_expected, "Salben").correct)
        self.assertTrue(grammar.check(plural_expected, "die Salben").correct)

    def test_cloze_does_not_duplicate_the_example_in_note(self) -> None:
        result = cloze.check(
            {"answer": "gehe", "sentence": "Ich gehe nach Hause."}, "gehe"
        )
        self.assertTrue(result.correct)
        self.assertEqual(result.note, "")


if __name__ == "__main__":
    unittest.main()
