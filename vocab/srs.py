"""Simplified SM-2 spaced repetition.

Quality scale (subset of classic 0-5):
    5 — exact correct answer on a hard (productive) exercise
    4 — correct answer
    3 — almost correct (typo / missing article): counts as passed, ease drops a bit
    1 — wrong

Stage drives exercise difficulty (see scheduler):
    0 — new or lapsed       -> recognition (multiple choice)
    1 — learning            -> flashcard DE->RU
    2 — consolidating       -> flashcard RU->DE, cloze
    3 — mature              -> cloze, grammar
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MIN_EASE = 1.3
LAPSE_RETRY_MINUTES = 10
FIRST_INTERVALS = [1.0, 3.0]  # days for the first two successful reviews


@dataclass
class Schedule:
    reps: int
    lapses: int
    ease: float
    interval_days: float
    due_at: str


def review(
    *, reps: int, lapses: int, ease: float, interval_days: float, quality: int,
    now: datetime | None = None,
) -> Schedule:
    now = now or datetime.now(timezone.utc)
    if quality < 3:
        reps = 0
        lapses += 1
        ease = max(MIN_EASE, ease - 0.2)
        interval_days = 0.0
        due = now + timedelta(minutes=LAPSE_RETRY_MINUTES)
    else:
        ease = max(MIN_EASE, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        if reps < len(FIRST_INTERVALS):
            interval_days = FIRST_INTERVALS[reps]
        else:
            interval_days = round(interval_days * ease, 1)
        reps += 1
        due = now + timedelta(days=interval_days)
    return Schedule(
        reps=reps, lapses=lapses, ease=round(ease, 2),
        interval_days=interval_days, due_at=due.isoformat(timespec="seconds"),
    )


def stage(reps: int, interval_days: float) -> int:
    if reps == 0:
        return 0
    if reps <= 2:
        return 1
    if reps <= 4 and interval_days < 21:
        return 2
    return 3
