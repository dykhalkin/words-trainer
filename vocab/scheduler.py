"""Transactional task, session, queue, and push scheduling."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from . import db, grading, reminders, srs, statistics
from .exercises import GENERATORS, GeneratedExercise
from .languages import ExerciseContext, language_spec, normalize_lemma

STAGE_TYPES = {
    0: ["choice"],
    1: ["flashcard_ru_de"],
    2: ["flashcard_ru_de", "cloze"],
    3: ["cloze", "grammar", "flashcard_ru_de"],
}
FALLBACK = ["flashcard_ru_de", "choice"]
DEFAULT_NEW_SCAN = 5
STARTED_SCAN_LIMIT = 100
TASK_TTL = timedelta(hours=24)
SESSION_IDLE_TTL = timedelta(minutes=30)


def _stage(row: dict[str, Any]) -> int:
    return srs.stage(int(row.get("reps") or 0), float(row.get("interval_days") or 0))


async def _candidate_rows(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    queue: str,
    deck_id: int | None = None,
    limit: int = STARTED_SCAN_LIMIT,
) -> list[dict[str, Any]]:
    deck_clause = " AND w.deck_id = %s" if deck_id is not None else ""
    params: list[Any] = [user_id]
    if deck_id is not None:
        params.append(deck_id)

    if queue in {"due", "review", "learning"}:
        due_clause = " AND p.due_at <= now()" if queue != "learning" else ""
        params.append(limit)
        rows = await db.fetch_all(
            conn,
            """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at
               FROM progress p JOIN words w ON w.id = p.word_id
               JOIN decks d ON d.id = w.deck_id
               WHERE w.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive"""
            + deck_clause
            + due_clause
            + " ORDER BY p.due_at NULLS FIRST, w.id LIMIT %s",
            tuple(params),
        )
        if queue == "review":
            return [row for row in rows if _stage(row) == 3]
        if queue == "learning":
            return [row for row in rows if _stage(row) < 3]
        return rows

    if queue == "new":
        params.append(limit)
        return await db.fetch_all(
            conn,
            """SELECT w.* FROM words w JOIN decks d ON d.id = w.deck_id
               LEFT JOIN progress p ON p.word_id = w.id
               WHERE w.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive"""
            + deck_clause
            + " AND p.word_id IS NULL ORDER BY w.id LIMIT %s",
            tuple(params),
        )
    raise ValueError(f"unsupported queue: {queue}")


async def _pick_row(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    queue: str,
    deck_id: int | None,
    rng: random.Random,
) -> dict[str, Any] | None:
    if queue == "auto":
        due = await _candidate_rows(
            conn, user_id=user_id, queue="due", deck_id=deck_id, limit=10
        )
        if due:
            return due[0]
        new = await _candidate_rows(
            conn,
            user_id=user_id,
            queue="new",
            deck_id=deck_id,
            limit=DEFAULT_NEW_SCAN,
        )
        return rng.choice(new) if new else None
    rows = await _candidate_rows(
        conn, user_id=user_id, queue=queue, deck_id=deck_id, limit=STARTED_SCAN_LIMIT
    )
    if not rows:
        return None
    return rng.choice(rows[:DEFAULT_NEW_SCAN]) if queue == "new" else rows[0]


async def _row_by_query(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    word_id: int | None,
    word_query: str | None,
    language: str | None,
) -> dict[str, Any] | None:
    if word_id is not None:
        return await db.fetch_one(
            conn,
            """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at
               FROM words w JOIN decks d ON d.id = w.deck_id
               LEFT JOIN progress p ON p.word_id = w.id
               WHERE w.user_id = %s AND w.id = %s
                 AND w.card_status = 'active' AND NOT d.is_archive""",
            (user_id, word_id),
        )
    if word_query is None:
        return None
    params: list[Any] = [user_id, f"%{word_query.strip()}%"]
    language_clause = ""
    if language:
        language_clause = " AND w.language = %s"
        params.append(language)
    return await db.fetch_one(
        conn,
        """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at
           FROM words w JOIN decks d ON d.id = w.deck_id
           LEFT JOIN progress p ON p.word_id = w.id
           WHERE w.user_id = %s AND w.lemma ILIKE %s
             AND w.card_status = 'active' AND NOT d.is_archive"""
        + language_clause
        + " ORDER BY length(w.lemma), w.id LIMIT 1",
        tuple(params),
    )


async def create_task(
    database: db.Database,
    user_id: int,
    rng: random.Random | None = None,
    *,
    word_id: int | None = None,
    word_query: str | None = None,
    language: str | None = None,
    task_type: str | None = None,
    queue: str = "auto",
    deck_id: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    rng = rng or random.Random()
    async with database.connection() as conn:
        async with conn.transaction():
            user_lock = await conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
            if not await user_lock.fetchone():
                raise LookupError("user not found")
            if session_id is not None:
                session_result = await conn.execute(
                    """SELECT * FROM sessions
                       WHERE id = %s AND user_id = %s AND status = 'open' FOR UPDATE""",
                    (session_id, user_id),
                )
                session = await session_result.fetchone()
                if not session:
                    return None
                if session["deck_id"] is not None:
                    deck_id = session["deck_id"]
                if (
                    session["kind"] == "micro"
                    and word_id is None
                    and word_query is None
                ):
                    queue = "due"
            if word_id is not None or word_query is not None:
                row = await _row_by_query(
                    conn,
                    user_id=user_id,
                    word_id=word_id,
                    word_query=word_query,
                    language=language,
                )
            else:
                row = await _pick_row(
                    conn, user_id=user_id, queue=queue, deck_id=deck_id, rng=rng
                )
            if not row:
                return None
            word = db.word_from_row(row)
            stage = _stage(row)
            context = ExerciseContext(user_id=user_id, language=word.language)
            allowed = set(language_spec(word.language).exercise_types)
            if task_type:
                if task_type not in allowed:
                    return None
                types = [task_type]
            else:
                types = [name for name in STAGE_TYPES[stage] if name in allowed]
                types.extend(name for name in FALLBACK if name in allowed and name not in types)
            produced: GeneratedExercise | None = None
            selected_type = ""
            for name in types:
                generator = GENERATORS.get(name)
                if not generator:
                    continue
                produced = await generator.generate(word, conn, rng, context)
                if produced is not None:
                    selected_type = name
                    break
                if task_type:
                    return None
            if produced is None:
                return None
            payload, expected = produced.payload, produced.expected
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            await conn.execute(
                """UPDATE answer_evaluations e SET status = 'discarded',
                          finished_at = now(), error = 'superseded by a new task'
                   FROM tasks t
                   WHERE e.task_id = t.id AND t.user_id = %s AND e.status = 'pending'
                     AND t.status = 'voided'""",
                (user_id,),
            )
            task_id = uuid.uuid4().hex[:16]
            await conn.execute(
                """INSERT INTO tasks(
                       id, user_id, word_id, session_id, type, payload, expected, expires_at,
                       response_mode, answer_language, grading_policy
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    task_id,
                    user_id,
                    row["id"],
                    session_id,
                    selected_type,
                    Jsonb(payload),
                    Jsonb(expected),
                    datetime.now(timezone.utc) + TASK_TTL,
                    produced.response_mode,
                    produced.answer_language,
                    produced.grading_policy,
                ),
            )
            if session_id:
                await conn.execute(
                    """UPDATE sessions SET last_activity_at = now()
                       WHERE id = %s AND user_id = %s AND status = 'open'""",
                    (session_id, user_id),
                )
            return {
                "task_id": task_id,
                "type": selected_type,
                "word": word.lemma,
                "word_id": row["id"],
                "language": word.language,
                "stage": stage,
                "response_mode": produced.response_mode,
                "answer_language": produced.answer_language,
                "grading_policy": produced.grading_policy,
                **payload,
            }


def _json_object(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def _locked_task(
    conn: AsyncConnection[dict[str, Any]], user_id: int, task_id: str
) -> dict[str, Any] | None:
    result = await conn.execute(
        """SELECT t.*, w.lemma, w.language, w.card, w.deck_id
           FROM tasks t JOIN words w ON w.id = t.word_id
           WHERE t.id = %s AND t.user_id = %s FOR UPDATE OF t""",
        (task_id, user_id),
    )
    return await result.fetchone()


def _expired(task: dict[str, Any]) -> bool:
    return task["status"] != "open" or task["expires_at"] <= datetime.now(timezone.utc)


async def _commit_review(
    conn: AsyncConnection[dict[str, Any]],
    task: dict[str, Any],
    *,
    answer: str,
    correct: bool,
    quality: int,
    expected: str,
    note: str = "",
    grading_source: str,
    evaluation_id: str | None = None,
    grader_feedback: str | None = None,
) -> dict[str, Any]:
    progress_result = await conn.execute(
        "SELECT * FROM progress WHERE word_id = %s FOR UPDATE", (task["word_id"],)
    )
    progress = await progress_result.fetchone()
    schedule = srs.review(
        reps=progress["reps"] if progress else 0,
        lapses=progress["lapses"] if progress else 0,
        ease=progress["ease"] if progress else 2.5,
        interval_days=progress["interval_days"] if progress else 0.0,
        quality=quality,
    )
    await conn.execute(
        """INSERT INTO progress(word_id, reps, lapses, ease, interval_days, due_at)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT(word_id) DO UPDATE SET
               reps = EXCLUDED.reps,
               lapses = EXCLUDED.lapses,
               ease = EXCLUDED.ease,
               interval_days = EXCLUDED.interval_days,
               due_at = EXCLUDED.due_at,
               updated_at = now()""",
        (
            task["word_id"], schedule.reps, schedule.lapses, schedule.ease,
            schedule.interval_days, schedule.due_at,
        ),
    )
    verdict = {
        "correct": correct,
        "expected": expected,
        "note": note,
        "grader_feedback": grader_feedback,
        "grading_source": grading_source,
        "answer_evaluation_id": evaluation_id,
        "next_review_at": schedule.due_at,
        "interval_days": schedule.interval_days,
        "stage": srs.stage(schedule.reps, schedule.interval_days),
    }
    await conn.execute(
        """UPDATE tasks SET status = 'answered', answered_at = now(), answer = %s,
               correct = %s, verdict = %s WHERE id = %s""",
        (answer, correct, Jsonb(verdict), task["id"]),
    )
    await conn.execute(
        """INSERT INTO reviews(
               user_id, word_id, deck_id, task_id, task_type, answer, correct, quality,
               grading_source, answer_evaluation_id, grader_feedback
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            task["user_id"], task["word_id"], task["deck_id"], task["id"],
            task["type"], answer, correct, quality, grading_source, evaluation_id,
            grader_feedback,
        ),
    )
    session_complete = False
    session_answered_count: int | None = None
    session_correct_count: int | None = None
    if task["session_id"]:
        updated = await conn.execute(
            """UPDATE sessions SET
                   answered_count = answered_count + 1,
                   correct_count = correct_count + %s,
                   last_activity_at = now()
               WHERE id = %s AND user_id = %s AND status = 'open'
               RETURNING *""",
            (int(correct), task["session_id"], task["user_id"]),
        )
        session = await updated.fetchone()
        if session:
            session_answered_count = session["answered_count"]
            session_correct_count = session["correct_count"]
        if session and session["target_count"] is not None:
            session_complete = session["answered_count"] >= session["target_count"]
            if session_complete:
                await conn.execute(
                    "UPDATE sessions SET status = 'completed', ended_at = now() WHERE id = %s",
                    (task["session_id"],),
                )
    card = _json_object(task["card"])
    return {
        "task_id": task["id"],
        "correct": correct,
        "expected": expected,
        "note": note,
        "grader_feedback": grader_feedback,
        "grading_source": grading_source,
        "answer_evaluation_id": evaluation_id,
        "word": task["lemma"],
        "translation": card.get("translation", ""),
        "next_review_at": schedule.due_at,
        "interval_days": schedule.interval_days,
        "stage": verdict["stage"],
        "session_complete": session_complete,
        "session_answered_count": session_answered_count,
        "session_correct_count": session_correct_count,
    }


def _evaluation_context(task: dict[str, Any]) -> dict[str, Any]:
    payload = _json_object(task["payload"])
    return {
        "language": task["language"],
        "answer_language": task["answer_language"],
        "exercise_type": task["type"],
        "prompt": payload.get("prompt", ""),
        "hint": payload.get("hint"),
        "requirements": {
            "response_mode": task["response_mode"],
            "grading_policy": task["grading_policy"],
        },
        "expected": _json_object(task["expected"]),
        "word": task["lemma"],
        "card": _json_object(task["card"]),
    }


async def begin_answer_submission(
    database: db.Database, user_id: int, task_id: str, answer: str
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            task = await _locked_task(conn, user_id, task_id)
            if not task:
                return {"error": "exercise expired", "task_id": task_id}
            if _expired(task):
                if task["status"] == "open":
                    await conn.execute(
                        "UPDATE tasks SET status = 'voided', voided_at = now() WHERE id = %s",
                        (task_id,),
                    )
                return {"error": "exercise expired", "task_id": task_id}
            pending = await db.fetch_one(
                conn,
                """SELECT * FROM answer_evaluations
                   WHERE task_id = %s AND status = 'pending' FOR UPDATE""",
                (task_id,),
            )
            if pending:
                return {
                    "task_id": task_id,
                    "pending": True,
                    "in_progress": True,
                    "evaluation_id": pending["id"],
                }
            expected_data = _json_object(task["expected"])
            checked = GENERATORS[task["type"]].check(expected_data, answer)
            if (
                task["response_mode"] == "free_text"
                and task["grading_policy"] == "tutor_on_mismatch"
                and not checked.correct
            ):
                evaluation_id = uuid.uuid4().hex
                context = _evaluation_context(task)
                await conn.execute(
                    """INSERT INTO answer_evaluations(
                           id, task_id, user_id, answer, answer_hash,
                           deterministic_result, context
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        evaluation_id, task_id, user_id, answer,
                        grading.answer_hash(answer),
                        Jsonb(
                            {
                                "correct": checked.correct,
                                "quality": checked.quality,
                                "expected": checked.expected,
                                "note": checked.note,
                            }
                        ),
                        Jsonb(context),
                    ),
                )
                return {
                    "task_id": task_id,
                    "pending": True,
                    "evaluation_id": evaluation_id,
                    "answer": answer,
                    "context": context,
                }
            return await _commit_review(
                conn,
                task,
                answer=answer,
                correct=checked.correct,
                quality=checked.quality,
                expected=checked.expected,
                note=checked.note,
                grading_source="deterministic",
            )


async def submit_answer(
    database: db.Database, user_id: int, task_id: str, answer: str
) -> dict[str, Any]:
    """Compatibility entry point; free-text mismatches now return a pending evaluation."""
    return await begin_answer_submission(database, user_id, task_id, answer)


async def finalize_tutor_evaluation(
    database: db.Database,
    user_id: int,
    evaluation_id: str,
    *,
    decision: str,
    feedback: str,
    model: str,
) -> dict[str, Any]:
    quality_by_decision = {"accepted": 4, "partial": 3, "rejected": 1}
    if decision not in quality_by_decision:
        raise ValueError("unsupported tutor decision")
    async with database.connection() as conn:
        async with conn.transaction():
            evaluation = await db.fetch_one(
                conn,
                """SELECT id, task_id FROM answer_evaluations
                   WHERE id = %s AND user_id = %s""",
                (evaluation_id, user_id),
            )
            if not evaluation:
                return {"stale": True, "evaluation_id": evaluation_id}
            task = await _locked_task(conn, user_id, evaluation["task_id"])
            result = await conn.execute(
                """SELECT * FROM answer_evaluations
                   WHERE id = %s AND user_id = %s FOR UPDATE""",
                (evaluation_id, user_id),
            )
            evaluation = await result.fetchone()
            if not evaluation or evaluation["status"] != "pending":
                return {"stale": True, "evaluation_id": evaluation_id}
            if not task or _expired(task):
                await conn.execute(
                    """UPDATE answer_evaluations SET status = 'discarded', finished_at = now()
                       WHERE id = %s""",
                    (evaluation_id,),
                )
                return {"stale": True, "evaluation_id": evaluation_id}
            quality = quality_by_decision[decision]
            expected_data = _json_object(task["expected"])
            canonical = GENERATORS[task["type"]].check(expected_data, "").expected
            await conn.execute(
                """UPDATE answer_evaluations SET status = 'succeeded', decision = %s,
                       quality = %s, feedback = %s, model = %s, finished_at = now()
                   WHERE id = %s""",
                (decision, quality, feedback[:500], model, evaluation_id),
            )
            return await _commit_review(
                conn,
                task,
                answer=evaluation["answer"],
                correct=decision != "rejected",
                quality=quality,
                expected=canonical,
                note=feedback[:500],
                grading_source="tutor",
                evaluation_id=evaluation_id,
                grader_feedback=feedback[:500],
            )


async def fail_tutor_evaluation(
    database: db.Database,
    user_id: int,
    evaluation_id: str,
    *,
    error: str,
    model: str,
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        result = await conn.execute(
            """UPDATE answer_evaluations
               SET status = 'failed', error = %s, model = %s, finished_at = now()
               WHERE id = %s AND user_id = %s AND status = 'pending'
               RETURNING *""",
            (error[:1000], model, evaluation_id, user_id),
        )
        return await result.fetchone()


async def evaluation_for_retry(
    database: db.Database, user_id: int, evaluation_id: str
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn,
            """SELECT * FROM answer_evaluations
               WHERE id = %s AND user_id = %s AND status = 'failed'""",
            (evaluation_id, user_id),
        )


async def retry_tutor_evaluation(
    database: db.Database, user_id: int, evaluation_id: str
) -> dict[str, Any]:
    evaluation = await evaluation_for_retry(database, user_id, evaluation_id)
    if not evaluation:
        return {"error": "evaluation unavailable", "evaluation_id": evaluation_id}
    return await begin_answer_submission(
        database, user_id, evaluation["task_id"], evaluation["answer"]
    )


async def override_answer_incorrect(
    database: db.Database, user_id: int, task_id: str
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            task = await _locked_task(conn, user_id, task_id)
            if not task or _expired(task):
                return {"error": "exercise expired", "task_id": task_id}
            evaluation = await db.fetch_one(
                conn,
                """SELECT * FROM answer_evaluations WHERE task_id = %s
                   ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (task_id,),
            )
            answer = evaluation["answer"] if evaluation else ""
            if evaluation and evaluation["status"] == "pending":
                await conn.execute(
                    """UPDATE answer_evaluations SET status = 'discarded', finished_at = now()
                       WHERE id = %s""",
                    (evaluation["id"],),
                )
            expected_data = _json_object(task["expected"])
            canonical = GENERATORS[task["type"]].check(expected_data, "").expected
            return await _commit_review(
                conn,
                task,
                answer=answer,
                correct=False,
                quality=1,
                expected=canonical,
                note="Ответ засчитан неверным по выбору ученика.",
                grading_source="learner_override",
                evaluation_id=evaluation["id"] if evaluation else None,
            )


async def get_open_task(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn,
            """SELECT id AS task_id, type, payload, word_id, session_id, response_mode,
                      answer_language, grading_policy, created_at, expires_at
               FROM tasks WHERE user_id = %s AND status = 'open' AND expires_at > now()""",
            (user_id,),
        )


async def task_context(
    database: db.Database, user_id: int, task_id: str
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        row = await db.fetch_one(
            conn,
            """SELECT t.*, w.lemma, w.card FROM tasks t JOIN words w ON w.id = t.word_id
               WHERE t.id = %s AND t.user_id = %s""",
            (task_id, user_id),
        )
    if not row:
        return None
    output = {
        "task_id": row["id"],
        "status": row["status"],
        "type": row["type"],
        "response_mode": row["response_mode"],
        "answer_language": row["answer_language"],
        "grading_policy": row["grading_policy"],
        "payload": _json_object(row["payload"]),
        "word": row["lemma"],
        "card": _json_object(row["card"]),
        "answer": row["answer"],
        "verdict": _json_object(row["verdict"]) if row["verdict"] else None,
    }
    if row["status"] == "answered":
        output["expected"] = _json_object(row["expected"])
    return output


async def sweep_expired(database: db.Database) -> int:
    async with database.connection() as conn:
        async with conn.transaction():
            failed = await conn.execute(
                """UPDATE answer_evaluations SET status = 'failed',
                          error = 'grading attempt timed out', finished_at = now()
                   WHERE status = 'pending' AND created_at < now() - interval '10 minutes'"""
            )
            expired = await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE status = 'open' AND expires_at <= now()"""
            )
            return failed.rowcount + expired.rowcount


async def start_session(
    database: db.Database,
    user_id: int,
    *,
    kind: str,
    deck_id: int | None = None,
    target_count: int | None = None,
) -> dict[str, Any]:
    if kind not in {"micro", "long"}:
        raise ValueError("session kind must be micro or long")
    if kind == "micro" and target_count is None:
        target_count = 5
    session_id = uuid.uuid4().hex[:16]
    async with database.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
            if deck_id is not None:
                deck_result = await conn.execute(
                    """SELECT id FROM decks
                       WHERE id = %s AND user_id = %s AND NOT is_archive""",
                    (deck_id, user_id),
                )
                if not await deck_result.fetchone():
                    raise ValueError("session deck must be an active learner-owned deck")
            await conn.execute(
                """UPDATE sessions SET status = 'stopped', ended_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            result = await conn.execute(
                """INSERT INTO sessions(id, user_id, kind, deck_id, target_count)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (session_id, user_id, kind, deck_id, target_count),
            )
            return await result.fetchone()


async def get_open_session(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn, "SELECT * FROM sessions WHERE user_id = %s AND status = 'open'", (user_id,)
        )


async def stop_session(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """UPDATE sessions SET status = 'stopped', ended_at = now()
                   WHERE user_id = %s AND status = 'open' RETURNING *""",
                (user_id,),
            )
            session = await result.fetchone()
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            return session


async def close_idle_sessions(database: db.Database) -> int:
    cutoff = datetime.now(timezone.utc) - SESSION_IDLE_TTL
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """UPDATE sessions SET status = 'expired', ended_at = now()
                   WHERE status = 'open' AND last_activity_at < %s RETURNING user_id""",
                (cutoff,),
            )
            rows = await result.fetchall()
            if rows:
                await conn.execute(
                    """UPDATE tasks SET status = 'voided', voided_at = now()
                       WHERE status = 'open' AND user_id = ANY(%s)""",
                    ([row["user_id"] for row in rows],),
                )
            return len(rows)


async def claim_push(
    database: db.Database, user_id: int, *, now: datetime | None = None, limit: int = 5
) -> dict[str, Any] | None:
    return await reminders.claim_due_delivery(database, user_id, now=now)


async def mark_delivery_sent(
    database: db.Database, delivery_id: int, telegram_message_id: int
) -> None:
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE deliveries SET status = 'sent', telegram_message_id = %s, sent_at = now()
               WHERE id = %s AND status = 'claimed'""",
            (telegram_message_id, delivery_id),
        )


async def mark_delivery_failed(
    database: db.Database, delivery_id: int, error: str, *, release: bool = False
) -> None:
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE deliveries SET status = %s, error = %s
               WHERE id = %s AND status = 'claimed'""",
            ("released" if release else "failed", error[:1000], delivery_id),
        )


async def claim_digest(
    database: db.Database, user_id: int, run_date: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Claim one weekly digest per learner/date across overlaps and restarts."""
    key = f"digest:{user_id}:{run_date}"
    async with database.connection() as conn:
        result = await conn.execute(
            """INSERT INTO deliveries(user_id, kind, idempotency_key, payload, claimed_at)
               VALUES (%s, 'digest', %s, %s, now())
               ON CONFLICT(idempotency_key) DO NOTHING RETURNING *""",
            (user_id, key, Jsonb(payload)),
        )
        return await result.fetchone()


async def list_due(database: db.Database, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    async with database.connection() as conn:
        return await _candidate_rows(conn, user_id=user_id, queue="due", limit=limit)


async def history(
    database: db.Database, user_id: int, *, word_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    word_clause = " AND r.word_id = %s" if word_id is not None else ""
    params: list[Any] = [user_id]
    if word_id is not None:
        params.append(word_id)
    params.append(limit)
    async with database.connection() as conn:
        return await db.fetch_all(
            conn,
            """SELECT r.*, w.lemma FROM reviews r JOIN words w ON w.id = r.word_id
               WHERE r.user_id = %s"""
            + word_clause
            + " ORDER BY r.created_at DESC LIMIT %s",
            tuple(params),
        )


async def stats(
    database: db.Database,
    user_id: int,
    *,
    deck_id: int | None = None,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    return await statistics.stats(
        database, user_id, deck_id=deck_id, days=days, now=now
    )
