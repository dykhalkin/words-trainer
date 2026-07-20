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


def stats_text(value: dict[str, Any]) -> str:
    recent = value["reviews_last_7d"]
    accuracy = recent["accuracy"]
    accuracy_text = "—" if accuracy is None else f"{round(accuracy * 100)}%"
    title = (
        f"Колода «{value['deck']['name']}»"
        if value.get("deck")
        else "Все активные колоды"
    )
    stages = value["by_stage"]
    lines = [
        title,
        f"Слов: {value['words_total']} (новых: {value['words_new']})",
        f"Изучаются: {value['words_studied']}; к повторению: {value['due_now']}",
        f"Этапы 0–3: {stages['0']} / {stages['1']} / {stages['2']} / {stages['3']}",
        f"Сегодня: {value['today']['review_attempts']} повторений, "
        f"{value['today']['unique_words']} уникальных слов",
        f"Точность за {value['days']} дней: {accuracy_text}",
    ]
    if not value.get("deck"):
        lines.append(
            f"Требуют исправления: {value['needs_fix']}; в архиве: {value['archived']}"
        )
    lines.append("\nПо дням:")
    lines.extend(
        f"{row['date']}: {row['review_attempts']} / {row['unique_words']}"
        for row in value["daily_activity"]
    )
    return "\n".join(lines)
