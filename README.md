# Telegram-тренажёр немецкой лексики

Приватный Telegram-бот для двух независимых учеников: интервальные повторения,
короткие push-сессии, длинная тренировка по запросу, объяснения преподавателя
OpenAI и планировщик-куратор. Проверка ответов всегда детерминированная.

PostgreSQL — единственный источник данных. CSV в `data/` используются только
для импорта и экспорта колод.

## Локальный запуск

Требуются Python 3.12 через `uv`, Docker/colima и Telegram bot token.

```bash
docker compose up -d
uv sync --frozen
uv run python cli.py user bootstrap --owner-chat-id <telegram_chat_id>
uv run python cli.py sync
uv run python -m bot
```

Бот явно загружает `~/.config/wordsbot/env` (другой путь задаётся через
`WORDSBOT_ENV_FILE`). Файл должен иметь права `0600`:

```dotenv
TELEGRAM_BOT_TOKEN=...
DATABASE_URL=postgresql://words_trainer:...@127.0.0.1:55432/words_trainer
OWNER_CHAT_ID=...
SPOUSE_CHAT_ID=...
OPENAI_API_KEY=... # необязательно: drills и pushes работают без него
WORDSBOT_BACKUP_DIR=... # необязательно: по умолчанию iCloud Drive
```

## Команды Telegram

- `/practice` — выбор колоды и длинная тренировка;
- `/stop` — остановить;
- `/decks` — колоды;
- `/stats` — общая и поколодная статистика;
- `/issues` — карточки, требующие исправления.

Push открывает короткую сессию из пяти заданий. Зрелые слова требуют ввода
текста; кнопки используются только там, где упражнение действительно имеет
варианты. Свободный текст при открытом задании всегда считается ответом, а при
его отсутствии направляется преподавателю.

В каждом упражнении можно перенести слово в защищённую колоду `Archive` или
пометить карточку как некорректную. Оба действия требуют подтверждения и не
засчитываются как повторение.

## Менеджерский CLI

CLI управляет данными и фоновыми jobs, но не запускает тренировки и не оценивает
ответы. Все команды возвращают JSON и принимают глобальный `--user` (по
умолчанию `owner`) и `--database-url`:

```bash
uv run python cli.py sync
uv run python cli.py deck list
uv run python cli.py stats --deck A2
uv run python cli.py issues
uv run python cli.py word-archive <word_id>
uv run python cli.py word-restore <word_id> --deck A2
uv run python cli.py word-flag <word_id> --reason "ошибка перевода"
uv run python cli.py word-fix <word_id> --card-json '{...}'
uv run python cli.py job list
uv run python cli.py job run push
```

`job run` только сохраняет запрос. Его забирает и выполняет живой процесс бота;
для отключённой job требуется `--force`.

Полный перечень: `uv run python cli.py --help`.

## Проверка и эксплуатация

```bash
UV_CACHE_DIR=/private/tmp/words-trainer-uv-cache \
  uv run python -m unittest discover -s tests -v
scripts/ops_check.sh
scripts/backup_postgres.sh
```

LaunchAgent-файлы находятся в `deploy/`. Установка выполняется
`scripts/install_launchd.sh`; восстановление проверяется по
[инструкции](docs/operations.md).

Основные гарантии: одна открытая задача и сессия на ученика, атомарная оценка и
действия с карточкой, изоляция по ученику и языку, сохранение истории при
архивации, локальные календарные дни в статистике, подтверждение AI-карточек
перед записью, лимит расходов OpenAI и persisted claims против повторных
push-сообщений и jobs.
