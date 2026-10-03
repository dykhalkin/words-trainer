"""Structured semantic answer-grading contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnswerGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "partial", "rejected"]
    feedback_ru: str = Field(min_length=1, max_length=500)
    target_reproduced: bool = Field(
        default=False,
        description="The learner used or clearly attempted the studied target, including a spelling error or inflection; false for a different expression.",
    )
    task_valid: bool = Field(
        default=True,
        description="The visible task is coherent and can be fairly assessed.",
    )
    comparison_expression: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        description="Short target-language alternative used in this accepted answer, suitable for comparison with the target; null if none.",
    )
    corrected_answer: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="For partial answers, the learner's answer with only its language errors corrected, preserving their chosen words; otherwise null.",
    )


@dataclass(frozen=True)
class Outcome:
    correct: bool
    quality: int
    assessed: bool
    neutral_reason: str | None
    target_reproduced: bool


def outcome(
    decision: str,
    *,
    response_mode: str,
    objective: str,
    target_reproduced: bool = True,
    task_valid: bool = True,
    assisted: bool = False,
    skipped: bool = False,
    early_review: bool = False,
) -> Outcome:
    """One policy for both local and semantic checks; the model never schedules reviews."""
    if decision not in {"accepted", "partial", "rejected"}:
        raise ValueError("unsupported tutor decision")
    correct = decision != "rejected"
    reason = None
    if skipped:
        reason = "skipped"
    elif not task_valid:
        reason = "invalid_task"
    elif assisted:
        reason = "assisted"
    elif objective in {"target_usage", "free_expression", "contrast"} or early_review:
        reason = "practice"
    elif correct and not target_reproduced:
        reason = "alternative"
    quality = {
        "accepted": 4 if response_mode == "choice" else 5,
        "partial": 3,
        "rejected": 1,
    }[decision]
    return Outcome(
        correct=correct,
        quality=0 if reason else quality,
        assessed=reason not in {"skipped", "invalid_task", "assisted"},
        neutral_reason=reason,
        target_reproduced=target_reproduced,
    )


def answer_hash(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def spelling_correction_note(answer: str, corrected: str | None) -> str | None:
    """Describe an explicit single-letter correction without inventing a language rule."""
    original, replacement = answer.strip(), (corrected or "").strip()
    if len(original) != len(replacement):
        return None
    differences = [(a, b) for a, b in zip(original, replacement) if a != b]
    if len(differences) != 1:
        return None
    before, after = differences[0]
    if not (before.isalpha() and after.isalpha()):
        return None
    if {"ä": "a", "ö": "o", "ü": "u"}.get(before.casefold()) == after.casefold():
        return f"Здесь пишется {after}, без умлаута."
    return f"Исправление: «{before}» → «{after}»."


def grader_input(context: dict[str, Any], answer: str) -> dict[str, Any]:
    """Keep hidden reference answers/card senses out of the semantic grading input."""
    visible = {
        key: context[key]
        for key in (
            "language",
            "answer_language",
            "exercise_type",
            "prompt",
            "objective",
            "visible_constraints",
            "options",
            "hint",
            "requirements",
        )
        if key in context
    }
    return {
        "exercise": visible,
        "learner_answer": answer,
        "tracked_expression_for_progress_only": context.get("word", ""),
    }
