"""Pure rendering helpers for drill messages."""

from __future__ import annotations

import re
from html import escape
from typing import Any

from vocab.exercises.base import same

TELEGRAM_TEXT_LIMIT = 4096
YOUGLISH_TRAILING_HELPERS = (
    "sein",
    "haben",
    "werden",
    "können",
    "müssen",
    "dürfen",
    "sollen",
    "wollen",
    "mögen",
    "möchten",
)


def telegram_text_chunks(text: str, *, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split plain text at paragraph boundaries for Telegram messages."""
    if limit < 1:
        raise ValueError("message limit must be positive")
    paragraphs = text.strip().split("\n\n")
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            split_at = max(
                paragraph.rfind("\n", 0, limit + 1),
                paragraph.rfind(" ", 0, limit + 1),
            )
            if split_at < 1:
                split_at = limit
            chunks.append(paragraph[:split_at].rstrip())
            paragraph = paragraph[split_at:].lstrip()
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def task_text(task: dict[str, Any]) -> str:
    lines = [str(task["prompt"])]
    if task.get("hint"):
        lines.extend(("", f"Подсказка: {task['hint']}"))
    if not task.get("options"):
        lines.extend(("", "Напишите ответ сообщением."))
    return "\n".join(lines)


def verdict_text(verdict: dict[str, Any]) -> str:
    """Render a Telegram HTML result with one answer block and a separate example."""
    reason = verdict.get("neutral_reason")
    partial = verdict.get("decision") == "partial" or verdict.get("quality") == 3
    if reason == "skipped":
        mark = "Пропущено без оценки"
    elif reason == "invalid_task":
        mark = "Задание не оценивается"
    elif partial:
        mark = "🟡 Почти правильно"
    elif verdict["correct"]:
        mark = "✅ Подходит" if reason == "alternative" else "✅ Верно"
    else:
        mark = "❌ Не совсем"
    answer = str(verdict.get("answer") or "").strip()
    expected = str(
        verdict.get("corrected_answer") or verdict.get("expected") or ""
    ).strip()
    blocks = [f"<b>{mark}</b>"]
    if reason == "invalid_task":
        answer_block = escape(answer)
    elif reason == "skipped" or not answer:
        answer_block = f"<b>{escape(expected)}</b>"
    elif partial or not verdict["correct"]:
        answer_block = f"<b>{escape(expected)}</b>"
        if not same(answer, expected):
            answer_block = f"{escape(answer)} → {answer_block}"
    else:
        answer_block = f"<b>{escape(answer)}</b>"
        if reason == "alternative" and expected and not same(answer, expected):
            answer_block += f"\nТакже: <b>{escape(expected)}</b>"
    translation = str(verdict.get("translation") or "").strip()
    if (
        translation
        and reason != "invalid_task"
        and not verdict.get("practice_only")
        and not same(translation, expected)
    ):
        answer_block += f"\n{escape(translation)}"
    if answer_block:
        blocks.append(answer_block)
    explanation = str(
        verdict.get("note") or verdict.get("context_explanation") or ""
    ).strip()
    if explanation:
        blocks.append(escape(explanation))
    tense_strip = verdict.get("tense_strip")
    if isinstance(tense_strip, dict):
        person = str(tense_strip.get("person") or "").strip()
        forms = tense_strip.get("forms")
        if person and isinstance(forms, list):
            rendered_forms = [
                f"{escape(str(item['tense']))}: {escape(str(item['form']))}"
                for item in forms
                if isinstance(item, dict) and item.get("tense") and item.get("form")
            ]
            if rendered_forms:
                blocks.append(
                    f"<b>Формы · {escape(person)}</b>\n" + "\n".join(rendered_forms)
                )
    example = str(verdict.get("example") or "").strip()
    if (
        example
        and not same(example, expected)
        and not (verdict["correct"] and same(example, answer))
    ):
        blocks.append(f"<blockquote>{escape(example)}</blockquote>")
    return "\n\n".join(blocks)


def youglish_query(value: dict[str, Any]) -> str:
    """Build a learner-facing YouGlish query without card metadata."""
    query = str(value.get("word") or "").strip()
    card = value.get("card")
    kind = value.get("kind")
    if not kind and isinstance(card, dict):
        kind = card.get("kind")
    example = value.get("example")
    if not example and isinstance(card, dict):
        example = card.get("example")
    if kind == "phrase":
        helper_pattern = "|".join(map(re.escape, YOUGLISH_TRAILING_HELPERS))
        without_helper = re.sub(
            rf"\s+(?:{helper_pattern})\s*[.!?]?$",
            "",
            query,
            flags=re.IGNORECASE,
        ).strip()
        helper_removed = without_helper != query
        if helper_removed:
            query = without_helper
            if re.search(r"(?:\.\.\.|…)", query):
                lexical_parts = re.findall(
                    r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", query, flags=re.UNICODE
                )
                query = lexical_parts[-1] if lexical_parts else query
        elif re.search(r"(?:\.\.\.|…)", query) and example:
            query = str(example).strip()
    if kind == "verb_prep":
        query = re.sub(
            r"\s+(?:\+\s*)?(?:Akk|Dat|Gen|Nom)\.?$",
            "",
            query,
            flags=re.IGNORECASE,
        )
    query = re.sub(r"(?:\.\.\.|…)", " ", query)
    return re.sub(r"\s+", " ", query).strip()


def session_summary(session: dict[str, Any] | None) -> str:
    if not session:
        return "Тренировка остановлена."
    answered = int(session.get("answered_count") or 0)
    correct = int(session.get("correct_count") or 0)
    assessed = int(session.get("assessed_count", answered))
    if not assessed:
        return f"Тренировка завершена: {answered} завершено, оценённых ответов нет."
    suffix = f" ({answered} завершено)" if assessed != answered else ""
    return f"Тренировка завершена: {correct}/{assessed} правильных{suffix}."


def stats_text(value: dict[str, Any]) -> str:
    recent = value["reviews_last_7d"]
    accuracy = recent["accuracy"]
    accuracy_text = "—" if accuracy is None else f"{round(accuracy * 100)}%"
    title = (
        f"Колода «{value['deck']['name']}»"
        if value.get("deck")
        else "Все активные колоды"
    )
    lines = [
        title,
        f"Активных карточек: {value['words_total']}",
        f"Новых: {value['words_new']}",
        f"Обучение начато: {value['words_studied']}",
        f"К повторению сейчас: {value['due_now']}",
        "\nСегодня:",
        f"Повторений: {value['today']['review_attempts']}",
        f"Уникальных карточек: {value['today']['unique_words']}",
        f"\nЗа {value['days']} дней:",
        f"Ответов: {recent['count']}",
        f"Верных: {recent['correct']}",
        f"Точность: {accuracy_text}",
    ]
    if not value.get("deck"):
        lines.append(
            f"Требуют исправления: {value['needs_fix']}; в архиве: {value['archived']}"
        )
    lines.append("\nЭтапы обучения:")
    for stage in value["learning_stages"]:
        lines.append(f"{stage['name']}: {stage['count']}")
    lines.append("\nПо дням:")
    lines.extend(
        f"{row['date']}: повторений — {row['review_attempts']}; "
        f"уникальных карточек — {row['unique_words']}"
        for row in value["daily_activity"]
    )
    return "\n".join(lines)


def learning_guide_text() -> str:
    return """Как работает обучение

Бот использует интервальные повторения: самостоятельное воспроизведение изучаемого слова увеличивает интервал до следующего задания, а ошибка возвращает карточку к простому упражнению. Подходящий синоним засчитывается как правильный ответ, но не увеличивает интервал изучаемого слова. В обычной тренировке основа — карточки, срок повторения которых уже наступил, а каждое четвёртое задание отводится новому слову, если такие есть. Если одна из очередей пуста, бот берёт слова из другой. Внутри очереди раньше появляются слова, которые ещё не показывались или показывались давно. Перезапуск сохраняет эту ротацию, даже если прошлое задание осталось без ответа.

При своевременных верных ответах карточка обычно доходит от первой тренировки до «Освоено» примерно за 12 дней: 1 день + 3 дня + около 8 дней. Это календарное ожидание между короткими повторениями, а не непрерывное время занятий.

Как считаются ответы
Самостоятельный ответ с изучаемым словом увеличивает текущую серию на один. Сюда относятся точный, правильный и принятый как «почти правильный» ответы. Почти правильный ответ продвигает слово, но будущие интервалы растут медленнее. Синонимы, пропуски и учебная практика не меняют эту серию.

Один незасчитанный ответ с любого этапа сразу обнуляет текущую серию и переводит карточку в «Повторное изучение». Несколько ошибок подряд не понижают её дальше: она остаётся там, а бот назначает новую попытку через 10 минут.

Переходы между этапами
Новые → Знакомство: 1 засчитанный ответ.
Новые → Повторное изучение: 1 незасчитанный ответ.
Повторное изучение → Знакомство: 1 засчитанный ответ; новая ошибка оставляет слово на текущем этапе.
Знакомство → Закрепление: серия должна достичь 3 засчитанных ответов после последней ошибки. При входе в «Знакомство» один уже есть, поэтому нужны ещё 2.
Закрепление → Освоено: интервал должен достичь 21 дня либо серия — 5 засчитанных ответов. Обычно хватает ещё 1 ответа; при медленном росте интервала нужны 2.
Освоено → Повторное изучение: 1 незасчитанный ответ. Чтобы вернуться, снова требуется 4–5 засчитанных ответов без новой ошибки.

Итого: от нового слова или последней ошибки до «Освоено» требуется 4 засчитанных ответа при обычном росте интервала либо 5 при замедленном. Любая новая ошибка запускает этот путь заново.

Новые
Карточку ещё не проходили. Первое задание — выбрать правильный перевод. После верного ответа начинается «Знакомство»; после ошибки — «Повторное изучение».
Время: до первой тренировки.

Повторное изучение
Сюда карточка попадает после ошибки. Бот снова даёт простое задание с вариантами ответа.
Время: повтор через 10 минут. Верный ответ переводит слово в «Знакомство».

Знакомство
Нужно самостоятельно вспомнить слово или фразу на изучаемом языке. Следующие интервалы — 1 день и 3 дня.
Время: около 4 дней и двух своевременных верных повторений до «Закрепления».

Закрепление
К воспроизведению добавляются задания с пропуском слова в примере. Для глаголов начинают появляться задания на Präsens, Perfekt и Präteritum. Интервал растёт примерно до 8 дней.
Время: обычно ещё около 8 дней и одного верного повторения до «Освоено»; после неточных ответов может потребоваться больше времени.

Освоено
Бот чередует воспроизведение, пропуски и грамматические задания, включая три времени глагола. У стадии нет даты окончания: карточка остаётся в долгосрочном повторении.
Время: первые интервалы обычно около 3 недель, затем 2 и 6 месяцев.

Как изучаются глаголы
После каждого ответа по обычному глаголу бот показывает короткую полоску третьего лица: Präsens, Perfekt и Präteritum. Она помогает пассивно запоминать основу, вспомогательный глагол, отделяемость и возвратность. Отдельные задания на формы времён появляются только на этапах «Закрепление» и «Освоено».

Как изучаются фразы
Фраза хранится и повторяется как одна карточка. Сначала нужно узнать её значение, затем воспроизвести целиком, вставить фразу в контекст и применить в короткой новой реплике. У устойчивого выражения обязательны ключевые слова; у сочетания допускается грамматически правильное изменение формы; у речевой конструкции сохраняется каркас, а содержимое вместо многоточия может меняться. Подходящий синоним считается правильным ответом. Бот объясняет разницу, а изучаемое слово позже повторяется отдельно; его интервал пока не растёт. Задание на применение прямо показывает нужную фразу и не меняет интервал.

Контекстная практика
Команда /context запускает отдельную бесконечную практику фраз и слов в новых ситуациях. Каждое задание заново создаётся LLM с учётом карточки, её стадии и недавних контекстов. Режим не изменяет этап, интервал, серию ответов и обычную статистику повторений; закончить его можно командой /stop.

Как учить близкие слова
/contrast слово / другое слово запускает три упражнения на различия. Сначала бот объясняет употребление и показывает примеры, затем предлагает конкретные ситуации. Можно выбрать первое выражение, второе или оба, если подходят оба. Это отдельная учебная тренировка: интервалы запоминания слов не меняются. После принятого альтернативного ответа такую тренировку можно открыть кнопкой «Различия · 3 задания».

Пропуск и сбой проверки не считаются ошибкой. Кнопка показа ответа завершает попытку без оценки.

Важно
Интервалы — ориентиры, а не обязательные сроки. Точный ответ ускоряет рост интервала, почти правильный — засчитывается, но замедляет его, неверный — сбрасывает текущую серию и назначает повтор через 10 минут. Быстрая тренировка обновляет график только для новых слов и тех, чей срок повторения уже наступил."""
