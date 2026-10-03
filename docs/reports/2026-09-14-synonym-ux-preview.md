> Исторический отчёт о прежней реализации. Актуальный результат: [переработка 2026-09-15](2026-09-15-synonym-rewrite.md).

# Synonym Practice UX Preview

Date: 2026-09-14

Source: local renderer preview from `bot.presentation.task_text`,
`bot.presentation.verdict_text`, `bot.presentation.session_summary`, and
the real inline keyboard builders in `bot.keyboards`.
This is not a live Telegram test and did not send messages to the production bot.

## Introduction

```text
zugänglich — доступный: есть доступ или возможность попасть внутрь

Einfach erklärt: Man kann hineingehen oder Zugang bekommen.

Der Garten ist für Besucher zugänglich.
```

Buttons:

```text
Попробовать с этим словом
Дальше
```

## Explicit Target Usage

```text
Используй zugänglich. Составь одно предложение по-немецки: сад открыт для посетителей.

Подсказка: есть доступ или возможность попасть внутрь

Напишите ответ сообщением.
```

Buttons:

```text
Подсказка · Показать ответ
Дальше
```

## Missing Required Target

```text
✅ Смысл передан
Слово: zugänglich
Ваш ответ: Der Garten ist für Besucher geöffnet.
Пример с изучаемым словом: Der Garten ist für Besucher zugänglich.
Смысл передан верно. Попробуй со словом zugänglich.
Целевое слово не засчитано в SRS; прогресс карточки не изменился.
```

Buttons:

```text
Попробовать с этим словом
Дальше
В живой речи (YouGlish)
Объяснить · В чём разница? · В архив · Ошибка
```

## Accepted Alternate

```text
✅ Подходит
Слово: zugänglich
Ваш ответ: Der Garten ist für Besucher geöffnet.
Ещё один вариант: Der Garten ist für Besucher zugänglich.
Целевое слово не засчитано в SRS; прогресс карточки не изменился.
```

## Hint

```text
Подсказка: Man kann hineingehen oder Zugang bekommen.
Ответ с подсказкой не изменит прогресс.
```

Third-stage hint label:

```text
Начало слова: zu…
Ответ с подсказкой не изменит прогресс.
```

## Answer Disclosure

```text
Ответ: zugänglich
После показа ответа можно потренироваться с этим словом или перейти дальше без оценки.
Пример: Der Garten ist für Besucher zugänglich.
```

Buttons:

```text
Попробовать с этим словом
Дальше
```

## Grader Unavailable

```text
Не удалось проверить ответ. Можно повторить проверку, изменить ответ или перейти дальше без оценки.
```

Buttons:

```text
Проверить снова · Дальше без оценки
Дальше
```

For unverified choice-option sets, `Дальше без оценки` does not write a failed
SRS review after grader/API failure; the backend finalizes that path as unassessed
and neutral because the option set was never semantically verified.

## Disputed Choice Fallback

```text
Что означает «benutzen»?

[пользоваться]
[использовать]
```

If the learner taps the exact stored answer, the result stays local. If the learner
taps another visible option from an unverified random set, Telegram shows:

```text
Проверяю ответ…
```

An accepted alternative is rendered as a correct visible outcome, but the target card
does not receive hidden recall credit:

```text
✅ Подходит
Слово: benutzen
Ваш ответ: использовать
Ещё один вариант: пользоваться
Целевое слово не засчитано в SRS; прогресс карточки не изменился.
```

## Multi-Correct Contrast

```text
✅ Подходит
Слово: zugänglich
Ваш ответ: verfügbar
Ещё один вариант: zugänglich
Целевое слово не засчитано в SRS; прогресс карточки не изменился.

Разница: Оба варианта возможны: verfügbar подчёркивает наличие, zugänglich — доступность для получения.
```

## Neutral Skip And Session Summary

```text
Пропущено
Ответ: zugänglich
Исход не входит в точность и не меняет прогресс.

Тренировка завершена: 2/2 правильных (3 завершено).
```

Neutral accepted alternatives complete the task and count as a correct visible outcome,
but they do not write `reviews` or update the hidden target's `progress`. The scheduler
sets `neutral_blocks_session` for such outcomes so the same card is not immediately
served again in the same session.

Telegram caller wiring now pauses automatic next-task delivery only when a verdict
contains `retry_available=true`. `Попробовать с этим словом` calls
`scheduler.start_target_retry(database, user_id, parent_task_id)`, while neutral
`Дальше` on an open task calls `scheduler.skip_task(database, user_id, task_id)`;
`Дальше` after an answered retry offer only sends the next ordinary task if no newer
open task already exists.

## Fresh Context Validation

Fresh LLM context practice is not saved straight from the generator. The backend
first asks a separate validator whether the visible Russian situation/task and
the reference answer are consistent and whether any target lemma or phrase is
being required invisibly. Invalid or unavailable validation falls back safely by
not creating that generated task; the learner may see no new context item rather
than being graded against a hidden requirement.

Final high validation remains pending: this preview is still renderer/static-flow
evidence, not a live Telegram walkthrough or production data audit.
