"""Pure rendering helpers for drill messages."""

from __future__ import annotations

from typing import Any


def task_text(task: dict[str, Any]) -> str:
    lines = [str(task["prompt"])]
    if task.get("hint"):
        lines.extend(("", f"Подсказка: {task['hint']}"))
    if not task.get("options"):
        lines.extend(("", "Напишите ответ сообщением."))
    return "\n".join(lines)


def verdict_text(verdict: dict[str, Any]) -> str:
    mark = "✅ Верно" if verdict["correct"] else "❌ Не совсем"
    lines = [mark, f"Ответ: {verdict['expected']}"]
    if verdict.get("note"):
        lines.append(str(verdict["note"]))
    return "\n".join(lines)


def session_summary(session: dict[str, Any] | None) -> str:
    if not session:
        return "Тренировка остановлена."
    answered = int(session.get("answered_count") or 0)
    correct = int(session.get("correct_count") or 0)
    return f"Тренировка завершена: {correct}/{answered} правильных."
