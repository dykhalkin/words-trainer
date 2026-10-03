from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message
from pydantic import SecretStr, ValidationError

from bot import drill
from bot.config import Settings
from bot.context import (
    ComparisonValidation,
    ContextExerciseOutput,
    ContextExerciseService,
)
from bot.keyboards import (
    AnswerCallback,
    FlowCallback,
    GradeCallback,
    grading_failure_keyboard,
    post_answer_keyboard,
)
from bot.presentation import session_summary, verdict_text
from tests.support import temporary_database
from vocab import db, grading, scheduler, words
from vocab.models import Phrase, VerbPrep, Word


def message():
    value = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=1001, type="private"),
        text="Question",
    )
    for method in ("answer", "edit_text", "edit_reply_markup"):
        object.__setattr__(value, method, AsyncMock())
    return value


def callback(value):
    return SimpleNamespace(message=value, answer=AsyncMock())


def button(markup, label):
    return next(
        item.callback_data
        for row in markup.inline_keyboard
        for item in row
        if item.text == label
    )


class SynonymFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = temporary_database()
        self.database = await self.temporary.__aenter__()
        self.owner = await words.bootstrap_user(
            self.database, name="owner", chat_id=1001
        )
        self.deck = await words.create_deck(
            self.database, self.owner["id"], "de", "Core"
        )
        self.target = await self.add(
            "zugänglich", "доступный", "Der Garten ist für Besucher zugänglich."
        )
        self.other = await self.add(
            "verfügbar", "доступный", "Das Produkt ist wieder verfügbar."
        )

    async def asyncTearDown(self):
        await self.temporary.__aexit__(None, None, None)

    async def add(self, lemma, translation, example):
        row, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=Word(
                lemma=lemma,
                kind="other",
                translation=translation,
                example=example,
                pronunciation="test",
            ),
        )
        return row

    async def start(self, **kwargs):
        return await scheduler.start_session(
            self.database,
            self.owner["id"],
            kind="long",
            deck_id=self.deck["id"],
            **kwargs,
        )

    async def task(self, session=None, task_type="flashcard_ru_de", word_id=None):
        return await scheduler.create_task(
            self.database,
            self.owner["id"],
            word_id=word_id or self.target["id"],
            task_type=task_type,
            session_id=session["id"] if session else None,
        )

    async def grade(
        self, task, answer="geöffnet", decision="accepted", target=False, valid=True
    ):
        pending = await scheduler.begin_answer_submission(
            self.database, self.owner["id"], task["task_id"], answer
        )
        self.assertTrue(pending["pending"])
        return await scheduler.finalize_tutor_evaluation(
            self.database,
            self.owner["id"],
            pending["evaluation_id"],
            decision=decision,
            feedback="Этот вариант подходит к ситуации.",
            model="test",
            target_reproduced=target,
            task_valid=valid,
            comparison_expression=answer
            if not target and decision == "accepted"
            else None,
        )

    async def counts(self):
        async with self.database.connection() as conn:
            reviews = await db.fetch_one(conn, "SELECT count(*) AS n FROM reviews")
            progress = await db.fetch_one(conn, "SELECT count(*) AS n FROM progress")
        return reviews["n"], progress["n"]

    async def test_accepted_alternative_is_visible_neutral_and_changes_next_session_exercise(
        self,
    ):
        session = await self.start()
        task = await self.task(session)
        verdict = await self.grade(task)
        self.assertTrue(verdict["correct"])
        self.assertTrue(verdict["assessed"])
        self.assertEqual(verdict["neutral_reason"], "alternative")
        self.assertEqual(verdict["answer"], "geöffnet")
        rendered = verdict_text(verdict)
        self.assertIn("<b>geöffnet</b>", rendered)
        self.assertIn("Также: <b>zugänglich</b>", rendered)
        self.assertLess(rendered.index("geöffnet"), rendered.index("zugänglich"))
        self.assertEqual(await self.counts(), (0, 0))
        saved = await scheduler.get_open_session(self.database, self.owner["id"])
        self.assertEqual(
            (saved["answered_count"], saved["assessed_count"], saved["correct_count"]),
            (1, 1, 1),
        )
        following = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        self.assertNotEqual(following["word_id"], task["word_id"])
        next_session = await self.start()
        # The regular queue selects the target again in a context, without manual task type.
        await scheduler.skip_task(
            self.database,
            self.owner["id"],
            (await self.task(next_session, word_id=self.other["id"]))["task_id"],
        )
        recalled = await scheduler.create_task(
            self.database, self.owner["id"], session_id=next_session["id"]
        )
        self.assertEqual(recalled["type"], "cloze")
        result = await scheduler.submit_answer(
            self.database, self.owner["id"], recalled["task_id"], "zugänglich"
        )
        self.assertFalse(result["neutral"])
        self.assertEqual(result["quality"], 5)
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE progress SET due_at = now() - interval '1 hour' WHERE word_id = %s",
                (self.target["id"],),
            )
        after_recall = await self.start()
        await scheduler.skip_task(
            self.database,
            self.owner["id"],
            (await self.task(after_recall, word_id=self.other["id"]))["task_id"],
        )
        ordinary = await scheduler.create_task(
            self.database, self.owner["id"], session_id=after_recall["id"]
        )
        self.assertEqual(ordinary["type"], "flashcard_ru_de")

    async def test_real_error_and_invalid_task_have_different_srs_effects(self):
        invalid = await self.grade(await self.task(), decision="rejected", valid=False)
        self.assertFalse(invalid["assessed"])
        self.assertEqual(await self.counts(), (0, 0))
        wrong = await self.grade(
            await self.task(), answer="morgen", decision="rejected"
        )
        self.assertFalse(wrong["neutral"])
        self.assertEqual(wrong["quality"], 1)
        self.assertEqual(await self.counts(), (1, 1))

    async def test_local_and_semantic_target_answers_have_same_quality(self):
        local = await scheduler.submit_answer(
            self.database,
            self.owner["id"],
            (await self.task())["task_id"],
            "zugänglich",
        )
        semantic = await self.grade(
            await self.task(), answer="Der Garten ist zugänglich.", target=True
        )
        self.assertEqual(local["quality"], semantic["quality"])
        self.assertEqual(local["quality"], 5)

    async def test_spelling_correction_is_partial_target_recall_in_the_rendered_reply(
        self,
    ):
        target = await self.add(
            "grundsätzlich",
            "в принципе; в основном",
            "Grundsätzlich bin ich damit einverstanden.",
        )
        session = await self.start(target_count=1)
        task = await self.task(session, word_id=target["id"])
        grader = SimpleNamespace(
            available=True,
            model="test",
            grade=AsyncMock(
                return_value=grading.AnswerGrade(
                    decision="partial",
                    feedback_ru="Здесь нужно исправить умлаут и количество букв n.",
                    target_reproduced=False,
                    corrected_answer="grundsätzlich",
                    comparison_expression="gründsätzlich",
                )
            ),
        )
        output = message()
        await drill.finish_answer(
            output,
            self.database,
            self.owner,
            task["task_id"],
            "gründsätzlich",
            edit=False,
            grader=grader,
        )
        saved = await scheduler.task_context(
            self.database, self.owner["id"], task["task_id"]
        )
        verdict = saved["verdict"]
        self.assertEqual(verdict["decision"], "partial")
        self.assertTrue(verdict["target_reproduced"])
        self.assertFalse(verdict["neutral"])
        self.assertEqual(verdict["quality"], 3)
        reply = output.answer.return_value.edit_text.await_args
        self.assertEqual(reply.kwargs["parse_mode"], "HTML")
        rendered = reply.args[0]
        self.assertIn("<b>🟡 Почти правильно</b>", rendered)
        self.assertIn("gründsätzlich → <b>grundsätzlich</b>", rendered)
        self.assertIn("Здесь пишется u, без умлаута.", rendered)
        self.assertNotIn("количество букв", rendered)
        self.assertEqual(rendered.count("grundsätzlich"), 1)
        self.assertNotIn("Также:", rendered)
        self.assertNotIn("повторим отдельно", rendered)
        self.assertNotIn(
            "Различия · 3 задания",
            [
                b.text
                for row in reply.kwargs["reply_markup"].inline_keyboard
                for b in row
            ],
        )
        self.assertEqual(await self.counts(), (1, 1))

    async def test_corrected_alternative_does_not_become_target_recall(self):
        task = await self.task()
        pending = await scheduler.begin_answer_submission(
            self.database, self.owner["id"], task["task_id"], "verfugbar"
        )
        verdict = await scheduler.finalize_tutor_evaluation(
            self.database,
            self.owner["id"],
            pending["evaluation_id"],
            decision="partial",
            feedback="Нужен умлаут.",
            model="test",
            target_reproduced=False,
            corrected_answer="verfügbar",
            comparison_expression="verfügbar",
        )
        self.assertFalse(verdict["target_reproduced"])
        self.assertTrue(verdict["neutral"])
        self.assertFalse(verdict["can_compare"])
        self.assertIn("verfugbar → <b>verfügbar</b>", verdict_text(verdict))
        self.assertEqual(await self.counts(), (0, 0))

    async def test_both_choice_directions_send_other_visible_options_to_semantic_grader(
        self,
    ):
        for task_type, answer in [
            ("choice_ru_de", "verfügbar"),
            ("choice", "доступный для посетителей"),
        ]:
            if task_type == "choice":
                await self.add("offen", answer, "Der Laden ist offen.")
            task = await self.task(task_type=task_type)
            self.assertIn(answer, task["options"])
            verdict = await self.grade(task, answer=answer)
            self.assertTrue(verdict["correct"])
            self.assertTrue(verdict["neutral"])
        self.assertEqual(await self.counts(), (0, 0))

    async def test_choice_button_uses_semantic_result_and_offers_comparison(self):
        session = await self.start()
        task = await self.task(session, task_type="choice_ru_de")
        grader = SimpleNamespace(
            available=True,
            model="test",
            grade=AsyncMock(
                return_value=grading.AnswerGrade(
                    decision="accepted",
                    feedback_ru="Подходит по смыслу.",
                    target_reproduced=False,
                    comparison_expression="verfügbar",
                )
            ),
        )
        output = message()
        await drill.answer_button(
            callback(output),
            AnswerCallback(
                task_id=task["task_id"], option=task["options"].index("verfügbar")
            ),
            self.database,
            self.owner,
            grader,
            None,
        )
        verdict = (
            await scheduler.task_context(
                self.database, self.owner["id"], task["task_id"]
            )
        )["verdict"]
        self.assertEqual(verdict["neutral_reason"], "alternative")
        markup = output.answer.return_value.edit_text.await_args.kwargs["reply_markup"]
        self.assertIsNotNone(button(markup, "Различия · 3 задания"))
        self.assertEqual(await self.counts(), (0, 0))

    async def test_sentence_answer_compares_its_expression_and_grader_has_no_hidden_card(
        self,
    ):
        session = await self.start()
        task = await self.task(session)
        answer = "Der Garten ist geöffnet."
        pending = await scheduler.begin_answer_submission(
            self.database, self.owner["id"], task["task_id"], answer
        )
        api_input = grading.grader_input(pending["context"], answer)
        self.assertNotIn("card", api_input["exercise"])
        self.assertNotIn("expected", api_input["exercise"])
        self.assertEqual(
            api_input["tracked_expression_for_progress_only"], "zugänglich"
        )
        await scheduler.finalize_tutor_evaluation(
            self.database,
            self.owner["id"],
            pending["evaluation_id"],
            decision="accepted",
            feedback="Подходит",
            model="test",
            target_reproduced=False,
            comparison_expression="geöffnet",
        )
        service, _ = self.service()
        await drill.flow_action(
            callback(message()),
            FlowCallback(task_id=task["task_id"], action="compare"),
            self.database,
            self.owner,
            service,
        )
        comparison = (
            await scheduler.get_open_session(self.database, self.owner["id"])
        )["comparison"]
        self.assertEqual(comparison["alternative"], "geöffnet")

    async def test_free_text_failure_skip_uses_rendered_button_and_never_records_error(
        self,
    ):
        session = await self.start(target_count=1)
        task = await self.task(session)
        pending = await scheduler.begin_answer_submission(
            self.database, self.owner["id"], task["task_id"], "geöffnet"
        )
        await scheduler.fail_tutor_evaluation(
            self.database,
            self.owner["id"],
            pending["evaluation_id"],
            error="unavailable",
            model="test",
        )
        markup = grading_failure_keyboard(task["task_id"], pending["evaluation_id"])
        data = GradeCallback.unpack(button(markup, "Дальше без оценки"))
        output = message()
        await drill.resolve_grade_failure(
            callback(output), data, self.database, self.owner, None, None
        )
        self.assertEqual(await self.counts(), (0, 0))
        task_context = await scheduler.task_context(
            self.database, self.owner["id"], task["task_id"]
        )
        self.assertEqual(task_context["verdict"]["neutral_reason"], "skipped")
        self.assertIn(
            "оценённых ответов нет", output.answer.await_args_list[-1].args[0]
        )

    async def test_stale_skip_and_duplicate_next_keep_the_active_successor(self):
        session = await self.start()
        task = await self.task(session)
        await self.grade(task)
        data = FlowCallback(task_id=task["task_id"], action="next")
        await asyncio.gather(
            *[
                drill.flow_action(
                    callback(message()), data, self.database, self.owner, None
                )
                for _ in range(2)
            ]
        )
        active = await scheduler.get_open_task(self.database, self.owner["id"])
        await drill.flow_action(
            callback(message()),
            FlowCallback(task_id=task["task_id"], action="skip"),
            self.database,
            self.owner,
            None,
        )
        self.assertEqual(
            active["task_id"],
            (await scheduler.get_open_task(self.database, self.owner["id"]))["task_id"],
        )
        async with self.database.connection() as conn:
            count = await db.fetch_one(
                conn,
                "SELECT count(*) AS n FROM tasks WHERE session_id = %s",
                (session["id"],),
            )
        self.assertEqual(count["n"], 2)

    async def test_skip_racing_pending_grade_has_one_terminal_result(self):
        session = await self.start()
        task = await self.task(session)
        pending = await scheduler.begin_answer_submission(
            self.database, self.owner["id"], task["task_id"], "geöffnet"
        )
        await asyncio.gather(
            scheduler.skip_task(self.database, self.owner["id"], task["task_id"]),
            scheduler.finalize_tutor_evaluation(
                self.database,
                self.owner["id"],
                pending["evaluation_id"],
                decision="accepted",
                feedback="Подходит",
                model="test",
                target_reproduced=False,
            ),
        )
        self.assertEqual(
            (await scheduler.get_open_session(self.database, self.owner["id"]))[
                "answered_count"
            ],
            1,
        )
        self.assertEqual(await self.counts(), (0, 0))

    async def test_legacy_assistance_cannot_be_cleared_by_creating_another_task(self):
        session = await self.start()
        first = await self.task(session, task_type="cloze")
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE tasks SET assistance_level = 1 WHERE id = %s",
                (first["task_id"],),
            )
        result = await scheduler.submit_answer(
            self.database, self.owner["id"], first["task_id"], "zugänglich"
        )
        self.assertEqual(result["neutral_reason"], "assisted")
        repeated = await self.task(session)
        result = await scheduler.submit_answer(
            self.database, self.owner["id"], repeated["task_id"], "zugänglich"
        )
        self.assertEqual(result["neutral_reason"], "assisted")
        self.assertEqual(await self.counts(), (0, 0))

    async def test_grammar_aliases_survive_and_phrase_usage_is_visible_and_neutral(
        self,
    ):
        row, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=VerbPrep(
                lemma="warten auf Akk",
                kind="verb_prep",
                translation="ждать",
                example="Ich warte auf den Bus.",
                pronunciation="test",
                verb="warten",
                preposition="auf",
                case="Akk",
            ),
        )
        task = await self.task(task_type="grammar", word_id=row["id"])
        self.assertIsNotNone(task)
        result = await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], "auf + Akkusativ"
        )
        self.assertEqual(result["quality"], 4)
        phrase, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=Phrase(
                lemma="auf jeden Fall",
                kind="phrase",
                phrase_type="fixed",
                translation="в любом случае",
                example="Ich komme auf jeden Fall.",
                pronunciation="test",
            ),
        )
        task = await self.task(task_type="phrase_context", word_id=phrase["id"])
        self.assertIn("auf jeden Fall", task["prompt"])
        result = await scheduler.submit_answer(
            self.database,
            self.owner["id"],
            task["task_id"],
            "Ich komme auf jeden Fall.",
        )
        self.assertTrue(result["neutral"])

    def service(
        self,
        answers=("first", "both", "second"),
        validations=None,
        validated_answers=None,
    ):
        calls = []
        generation_count = 0
        validation_count = 0

        class Result:
            context_wrapper = SimpleNamespace(
                usage=SimpleNamespace(input_tokens=10, output_tokens=10)
            )

            def __init__(self, value):
                self.value = value

            def final_output_as(self, output_type, **kwargs):
                assert isinstance(self.value, output_type)
                return self.value

        async def run(agent, prompt, **kwargs):
            nonlocal generation_count, validation_count
            calls.append((agent.name, json.loads(prompt)))
            if agent.output_type is ComparisonValidation:
                valid = validations[validation_count] if validations else True
                key = (validated_answers or answers)[
                    validation_count % len(validated_answers or answers)
                ]
                validation_count += 1
                return Result(
                    ComparisonValidation(
                        valid=valid,
                        first_fits=key in {"first", "both"},
                        second_fits=key in {"second", "both"},
                        explanation_ru="Пояснение различия двух выражений.",
                        reference_answer="Der Garten ist zugänglich.",
                    )
                )
            index = generation_count
            generation_count += 1
            return Result(
                ContextExerciseOutput(
                    mode="reply",
                    situation_ru=f"Тестовая ситуация {index}",
                    task=f"Выберите выражение для контекста {index}.",
                    reference_answer="Der Garten ist zugänglich.",
                    required_elements=[],
                    context_tag=f"test:{index}",
                    explanation_ru="Пояснение различия двух выражений.",
                    comparison_intro_ru="Объяснение употребления двух слов с примерами.",
                )
            )

        service = ContextExerciseService(
            Settings(
                TELEGRAM_BOT_TOKEN="token",
                DATABASE_URL="postgresql://unused",
                OWNER_CHAT_ID=1001,
                OPENAI_API_KEY=SecretStr("test-key"),
            ),
            runner=run,
        )
        return service, calls

    async def comparison(self):
        return await scheduler.start_session(
            self.database,
            self.owner["id"],
            kind="context",
            target_count=3,
            comparison={"word_id": self.target["id"], "alternative": "verfügbar"},
        )

    async def test_comparison_teaches_then_runs_three_contexts_without_changing_word_progress(
        self,
    ):
        session = await self.comparison()
        service, calls = self.service()
        previous = None
        for index, correct_index in enumerate((0, 2, 1)):
            task = await service.create_task(
                self.database, self.owner, session["id"], previous_task_id=previous
            )
            self.assertEqual(task["objective"], "contrast")
            self.assertEqual(
                task["options"],
                ["Только zugänglich", "Только verfügbar", "Оба подходят"],
            )
            self.assertEqual("Объяснение употребления" in task["prompt"], index == 0)
            result = await scheduler.submit_answer(
                self.database,
                self.owner["id"],
                task["task_id"],
                task["options"][correct_index],
            )
            self.assertTrue(result["correct"])
            self.assertTrue(result["neutral"])
            self.assertEqual(result["session_complete"], index == 2)
            previous = task["task_id"]
        self.assertEqual(await self.counts(), (0, 0))
        self.assertIsNone(
            await scheduler.get_open_session(self.database, self.owner["id"])
        )
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[2][1]["practice_step"], 1)
        self.assertEqual(
            calls[2][1]["recent_contexts"][0]["comparison_answer"], "first"
        )

    async def test_unfair_comparison_is_retried_but_never_persisted(self):
        session = await self.comparison()
        service, calls = self.service(validations=[False, False])
        with self.assertRaisesRegex(ValueError, "fair comparison"):
            await service.create_task(self.database, self.owner, session["id"])
        self.assertIsNone(
            await scheduler.get_open_task(self.database, self.owner["id"])
        )
        self.assertEqual(len(calls), 4)
        self.assertEqual(await self.counts(), (0, 0))

    async def test_comparison_wrong_choice_uses_verified_key_and_shows_example(self):
        session = await self.comparison()
        service, _ = self.service(answers=["both"])
        task = await service.create_task(self.database, self.owner, session["id"])
        result = await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], task["options"][0]
        )
        self.assertFalse(result["correct"])
        self.assertTrue(result["assessed"])
        self.assertTrue(result["neutral"])
        self.assertEqual(result["expected"], "Оба подходят")
        rendered = verdict_text(result)
        self.assertIn("Пояснение различия", rendered)
        self.assertIn("Der Garten ist zugänglich.", rendered)
        self.assertEqual(await self.counts(), (0, 0))

    async def test_validator_failure_does_not_persist_exercise(self):
        session = await self.comparison()
        service, _ = self.service()
        service._validate_comparison = AsyncMock(
            side_effect=RuntimeError("API unavailable")
        )
        with self.assertRaisesRegex(RuntimeError, "API unavailable"):
            await service.create_task(self.database, self.owner, session["id"])
        self.assertIsNone(
            await scheduler.get_open_task(self.database, self.owner["id"])
        )

    async def test_validator_independently_sets_key_without_seeing_draft_reference(
        self,
    ):
        session = await self.comparison()
        service, calls = self.service(answers=["first"], validated_answers=["both"])
        task = await service.create_task(self.database, self.owner, session["id"])
        result = await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], "Оба подходят"
        )
        self.assertTrue(result["correct"])
        self.assertEqual(
            result["context_explanation"], "Пояснение различия двух выражений."
        )
        validator_input = calls[1][1]
        self.assertNotIn("generated", validator_input)
        self.assertNotIn("comparison_answer", validator_input)
        self.assertNotIn("reference_answer", validator_input)
        self.assertEqual(len(calls), 2)

    async def test_late_generation_failure_cannot_stop_newer_session(self):
        await self.comparison()

        async def fail_after_new_session(*args, **kwargs):
            await self.start()
            await self.task()
            raise RuntimeError("old generation failed")

        service = SimpleNamespace(
            available=True, create_task=AsyncMock(side_effect=fail_after_new_session)
        )
        with self.assertLogs("bot.drill", level="ERROR"):
            await drill.send_task(message(), self.database, self.owner, service)
        self.assertEqual(
            (await scheduler.get_open_session(self.database, self.owner["id"]))["kind"],
            "long",
        )
        self.assertIsNotNone(
            await scheduler.get_open_task(self.database, self.owner["id"])
        )

    async def test_comparison_command_checks_availability_before_replacing_session(
        self,
    ):
        session = await self.start()
        output = message()
        object.__setattr__(output, "text", "/contrast zugänglich / verfügbar")
        await drill.contrast_practice(output, self.database, self.owner, None)
        self.assertEqual(
            (await scheduler.get_open_session(self.database, self.owner["id"]))["id"],
            session["id"],
        )
        service, _ = self.service()
        await drill.contrast_practice(output, self.database, self.owner, service)
        self.assertEqual(
            (await scheduler.get_open_task(self.database, self.owner["id"]))["payload"][
                "objective"
            ],
            "contrast",
        )

    async def test_failed_comparison_can_be_retried_with_the_same_button(self):
        session = await self.start()
        task = await self.task(session)
        await self.grade(task)
        data = FlowCallback(task_id=task["task_id"], action="compare")
        failing, _ = self.service(validations=[False, False])
        with self.assertLogs("bot.drill", level="ERROR"):
            await drill.flow_action(
                callback(message()), data, self.database, self.owner, failing
            )
        self.assertIsNone(
            await scheduler.get_open_session(self.database, self.owner["id"])
        )
        working, _ = self.service()
        await drill.flow_action(
            callback(message()), data, self.database, self.owner, working
        )
        self.assertEqual(
            (await scheduler.get_open_task(self.database, self.owner["id"]))["payload"][
                "objective"
            ],
            "contrast",
        )

    async def test_comparison_callback_is_reachable_and_duplicate_does_not_restart_session(
        self,
    ):
        session = await self.start()
        task = await self.task(session)
        verdict = await self.grade(task)
        markup = post_answer_keyboard(
            task["task_id"], verdict["word"], can_compare=verdict["can_compare"]
        )
        data = FlowCallback.unpack(button(markup, "Различия · 3 задания"))
        service, calls = self.service()
        await drill.flow_action(
            callback(message()), data, self.database, self.owner, service
        )
        first = await scheduler.get_open_session(self.database, self.owner["id"])
        active = await scheduler.get_open_task(self.database, self.owner["id"])
        await drill.flow_action(
            callback(message()), data, self.database, self.owner, service
        )
        self.assertEqual(
            first["id"],
            (await scheduler.get_open_session(self.database, self.owner["id"]))["id"],
        )
        self.assertEqual(
            active["task_id"],
            (await scheduler.get_open_task(self.database, self.owner["id"]))["task_id"],
        )
        self.assertEqual(len(calls), 2)

    async def test_comparison_cannot_use_another_learners_word(self):
        other = await words.bootstrap_user(self.database, name="other", chat_id=1002)
        with self.assertRaises(ValueError):
            await scheduler.start_session(
                self.database,
                other["id"],
                kind="context",
                target_count=3,
                comparison={"word_id": self.target["id"], "alternative": "verfügbar"},
            )

    def test_free_context_schema_rejects_hidden_requirements(self):
        with self.assertRaises(ValidationError):
            ContextExerciseOutput(
                mode="reply",
                situation_ru="Встреча",
                task="Ответь другу",
                reference_answer="Ja!",
                required_elements=["auf jeden Fall"],
                context_tag="test:one",
                explanation_ru="Объяснение",
            )


class SynonymMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_populated_v14_upgrade_keeps_counts_and_voids_unsafe_open_tasks(self):
        async with temporary_database(migrate=False) as database:
            async with database.connection() as conn:
                await conn.execute(
                    "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
                for path in sorted(Path(db.MIGRATIONS_DIR).glob("*.sql")):
                    if path.name.startswith("015_"):
                        break
                    await conn.execute(path.read_text(), prepare=False)
                    await conn.execute(
                        "INSERT INTO schema_migrations(version) VALUES (%s)",
                        (path.name,),
                    )
                await conn.execute(
                    "INSERT INTO users(id, name, chat_id) VALUES (1, 'owner', 1001)"
                )
                await conn.execute(
                    "INSERT INTO decks(id,user_id,language,name,normalized_name) VALUES (1,1,'de','Core','core')"
                )
                await conn.execute("""INSERT INTO words(id,user_id,deck_id,language,lemma,lemma_key,kind,card)
                    VALUES (1,1,1,'de','zugänglich','zugänglich','other','{"lemma":"zugänglich","example":"Der Garten ist zugänglich."}')""")
                await conn.execute(
                    "INSERT INTO sessions(id,user_id,kind,answered_count,correct_count) VALUES ('old',1,'long',5,4)"
                )
                await conn.execute("""INSERT INTO tasks(id,user_id,word_id,session_id,type,payload,expected,response_mode,grading_policy)
                    VALUES ('old-task',1,1,'old','choice_ru_de','{}','{}','choice','deterministic')""")
            await database.migrate()
            async with database.connection() as conn:
                session = await db.fetch_one(
                    conn, "SELECT * FROM sessions WHERE id = 'old'"
                )
                task = await db.fetch_one(
                    conn, "SELECT status FROM tasks WHERE id = 'old-task'"
                )
            self.assertEqual(session["assessed_count"], 5)
            self.assertEqual(
                session_summary(session), "Тренировка завершена: 4/5 правильных."
            )
            self.assertEqual(task["status"], "voided")
