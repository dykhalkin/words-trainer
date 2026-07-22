"""Structured semantic answer-grading contracts."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnswerGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "partial", "rejected"]
    feedback_ru: str = Field(min_length=1, max_length=500)


def answer_hash(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def grader_input(context: dict[str, Any], answer: str) -> dict[str, Any]:
    """Build the bounded grader payload; the learner answer remains untrusted data."""
    return {
        "exercise": context,
        "learner_answer": answer,
    }
