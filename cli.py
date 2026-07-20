#!/usr/bin/env python3
"""JSON CLI over the same async PostgreSQL core used by the Telegram bot."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from vocab import db, scheduler, storage, words

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("WORDS_DATA", ROOT / "data"))
TASK_TYPES = ["choice", "flashcard_de_ru", "flashcard_ru_de", "cloze", "grammar"]
TASK_QUEUES = ["auto", "due", "new", "learning", "review"]


def out(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


async def require_user(database: db.Database, identifier: str) -> dict[str, Any]:
    user = await words.get_user(database, identifier)
    if not user:
        raise LookupError(f"user not found: {identifier}; run 'user bootstrap' first")
    return user


async def resolve_deck(
    database: db.Database, user_id: int, value: str | None, language: str | None = None
) -> dict[str, Any] | None:
    if value is None:
        return None
    deck: int | str = int(value) if value.isdigit() else value
    found = await words.get_deck(database, user_id, deck, language)
    if not found:
        raise LookupError(f"deck not found: {value}")
    return found


async def command_user(database: db.Database, args: argparse.Namespace) -> Any:
    if args.action == "bootstrap":
        owner_chat_id = args.owner_chat_id or os.environ.get("OWNER_CHAT_ID")
        spouse_chat_id = args.spouse_chat_id or os.environ.get("SPOUSE_CHAT_ID")
        if not owner_chat_id:
            raise ValueError("OWNER_CHAT_ID or --owner-chat-id is required")
        created = [
            await words.bootstrap_user(
                database,
                name="owner",
                chat_id=int(owner_chat_id),
                timezone=args.timezone,
                llm_monthly_cap_usd=args.llm_cap,
            )
        ]
        if spouse_chat_id:
            created.append(
                await words.bootstrap_user(
                    database,
                    name="spouse",
                    chat_id=int(spouse_chat_id),
                    timezone=args.timezone,
                    llm_monthly_cap_usd=args.llm_cap,
                )
            )
        return {"users": created, "spouse_pending": not bool(spouse_chat_id)}
    if args.action == "list":
        return await words.list_users(database)
    user = await require_user(database, args.user)
    updates = {
        key: value
        for key, value in {
            "timezone": args.timezone,
            "min_push_interval_minutes": args.min_push_interval,
            "quiet_start": args.quiet_start,
            "quiet_end": args.quiet_end,
            "daily_new_limit": args.daily_new_limit,
            "llm_monthly_cap_usd": args.llm_cap,
        }.items()
        if value is not None
    }
    if not updates:
        return user
    assignments = ", ".join(f"{key} = %s" for key in updates)
    async with database.connection() as conn:
        result = await conn.execute(
            f"UPDATE users SET {assignments}, updated_at = now() WHERE id = %s RETURNING *",
            (*updates.values(), user["id"]),
        )
        return await result.fetchone()


async def command_deck(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    if args.action == "list":
        return {"decks": await words.list_decks(database, user["id"])}
    if args.action == "create":
        return await words.create_deck(database, user["id"], args.language, args.name)
    if args.action == "rename":
        return await words.rename_deck(database, user["id"], args.deck_id, args.name)
    if args.action == "delete":
        return await words.delete_deck(database, user["id"], args.deck_id)
    return await words.move_word(database, user["id"], args.word_id, args.deck_id)


async def command_import(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    return await storage.import_csv(
        database,
        Path(args.path),
        user_id=user["id"],
        deck_name=args.deck,
        language=args.language,
    )


async def command_sync(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    results = []
    for path in sorted(DATA_DIR.glob("*.csv")):
        results.append(
            await storage.import_csv(
                database,
                path,
                user_id=user["id"],
                deck_name=path.stem,
                language="de",
            )
        )
    return {"imports": results}


async def command_export(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    deck = await resolve_deck(database, user["id"], args.deck)
    return await storage.export_deck(
        database, Path(args.path), user_id=user["id"], deck_id=deck["id"]
    )


async def command_task(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    deck = await resolve_deck(database, user["id"], getattr(args, "deck", None))
    queue = getattr(args, "queue", "auto")
    if args.command.startswith("task-"):
        queue = args.command.removeprefix("task-")
    task = await scheduler.create_task(
        database,
        user["id"],
        word_query=getattr(args, "word", None),
        task_type=getattr(args, "type", None),
        queue=queue,
        deck_id=deck["id"] if deck else None,
        session_id=getattr(args, "session_id", None),
    )
    if not task:
        raise LookupError("no task available")
    return task


async def command_answer(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    return await scheduler.submit_answer(database, user["id"], args.task_id, args.answer)


async def command_word(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    word = await words.get_word(database, user["id"], args.query, args.language)
    if not word:
        raise LookupError(f"word not found: {args.query}")
    result = dataclasses.asdict(word)
    async with database.connection() as conn:
        result["progress"] = await db.fetch_one(
            conn, "SELECT * FROM progress WHERE word_id = %s", (word.db_id,)
        )
    result.update({"word_id": word.db_id, "deck_id": word.deck_id, "language": word.language})
    return result


async def command_session(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    if args.action == "stop":
        return await scheduler.stop_session(database, user["id"])
    deck = await resolve_deck(database, user["id"], args.deck)
    return await scheduler.start_session(
        database,
        user["id"],
        kind=args.kind,
        deck_id=deck["id"] if deck else None,
        target_count=args.target_count,
    )


async def command_push(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    if args.action == "compose":
        return await scheduler.compose_push(database, user["id"], args.limit)
    return await scheduler.claim_push(database, user["id"], limit=args.limit)


async def command_push_plan(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    async with database.connection() as conn:
        if args.action == "get":
            return await db.fetch_one(
                conn,
                """SELECT * FROM curator_plans WHERE user_id = %s AND kind = 'plan'
                   ORDER BY run_date DESC LIMIT 1""",
                (user["id"],),
            )
        payload = json.loads(args.json)
        result = await conn.execute(
            """INSERT INTO curator_plans(user_id, run_date, kind, plan)
               VALUES (%s, %s, 'plan', %s)
               ON CONFLICT(user_id, run_date, kind) DO UPDATE
               SET plan = EXCLUDED.plan, created_at = now() RETURNING *""",
            (user["id"], date.today(), Jsonb(payload)),
        )
        return await result.fetchone()


async def command_pending(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    if args.command == "propose-words":
        cards = json.loads(args.cards_json)
        return await words.stage_cards(
            database,
            user_id=user["id"],
            language=args.language,
            deck_name=args.deck,
            cards=cards,
        )
    if args.command == "confirm-pending":
        return await words.commit_pending(
            database, user["id"], args.pending_id, move_existing=args.move_existing
        )
    return {"rejected": await words.reject_pending(database, user["id"], args.pending_id)}


async def command_practice(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    print("Тренировка. Пустой ввод или 'q' — выход.\n")
    session = await scheduler.start_session(database, user["id"], kind="long")
    while True:
        task = await scheduler.create_task(database, user["id"], session_id=session["id"])
        if not task:
            print("Сейчас больше нечего изучать.")
            break
        print(f"[{task['type']}] {task['prompt']}")
        for index, option in enumerate(task.get("options", []), 1):
            print(f"  {index}. {option}")
        if task.get("hint"):
            print(f"  подсказка: {task['hint']}")
        answer = (await asyncio.to_thread(input, "> ")).strip()
        if not answer or answer.lower() in {"q", "quit", "exit"}:
            break
        result = await scheduler.submit_answer(database, user["id"], task["task_id"], answer)
        mark = "✅" if result["correct"] else "❌"
        note = f" — {result['note']}" if result.get("note") else ""
        print(f"{mark} {result['expected']}{note}\n")
    return await scheduler.stop_session(database, user["id"])


async def run(args: argparse.Namespace) -> int:
    database = db.Database(args.database_url)
    try:
        await database.open()
        if args.command == "migrate":
            await database.migrate()
            result: Any = {"migrated": True}
        elif args.command == "user":
            result = await command_user(database, args)
        elif args.command == "deck":
            result = await command_deck(database, args)
        elif args.command == "import":
            result = await command_import(database, args)
        elif args.command == "sync":
            result = await command_sync(database, args)
        elif args.command == "export":
            result = await command_export(database, args)
        elif args.command in {"task", "task-new", "task-learning", "task-review"}:
            result = await command_task(database, args)
        elif args.command == "answer":
            result = await command_answer(database, args)
        elif args.command == "due":
            user = await require_user(database, args.user)
            due = await scheduler.list_due(database, user["id"], args.limit)
            result = {
                "due": [
                    {key: row[key] for key in ("id", "lemma", "kind", "language", "due_at", "reps")}
                    for row in due
                ]
            }
        elif args.command == "stats":
            user = await require_user(database, args.user)
            result = await scheduler.stats(database, user["id"])
        elif args.command == "word":
            result = await command_word(database, args)
        elif args.command == "task-context":
            user = await require_user(database, args.user)
            result = await scheduler.task_context(database, user["id"], args.task_id)
        elif args.command == "history":
            user = await require_user(database, args.user)
            word_id = None
            if args.word:
                found = await words.get_word(database, user["id"], args.word)
                if not found:
                    raise LookupError(f"word not found: {args.word}")
                word_id = found.db_id
            result = {
                "reviews": await scheduler.history(
                    database, user["id"], word_id=word_id, limit=args.limit
                )
            }
        elif args.command == "curator-run":
            user = await require_user(database, args.user)
            from vocab import curator as curator_core

            if args.dry_run:
                result = await curator_core.analyze(database, user["id"])
            else:
                from bot.config import load_settings
                from bot.curator import CuratorService

                result = await CuratorService(load_settings()).run_for(
                    database, user, kind=args.kind
                )
        elif args.command == "session":
            result = await command_session(database, args)
        elif args.command == "push":
            result = await command_push(database, args)
        elif args.command == "push-plan":
            result = await command_push_plan(database, args)
        elif args.command in {"propose-words", "confirm-pending", "reject-pending"}:
            result = await command_pending(database, args)
        elif args.command == "practice":
            result = await command_practice(database, args)
        else:
            raise ValueError(f"unsupported command: {args.command}")
        if args.command != "practice":
            out(result)
        return 0
    except Exception as exc:
        out({"error": str(exc), "type": type(exc).__name__})
        return 1
    finally:
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private Telegram vocabulary trainer")
    parser.add_argument("--database-url", default=db.database_url())
    parser.add_argument("--user", default="owner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")

    user = sub.add_parser("user")
    user_sub = user.add_subparsers(dest="action", required=True)
    bootstrap = user_sub.add_parser("bootstrap")
    bootstrap.add_argument("--owner-chat-id", type=int)
    bootstrap.add_argument("--spouse-chat-id", type=int)
    bootstrap.add_argument("--timezone", default="Europe/Berlin")
    bootstrap.add_argument("--llm-cap", type=float, default=20.0)
    user_sub.add_parser("list")
    settings = user_sub.add_parser("settings")
    settings.add_argument("--timezone")
    settings.add_argument("--min-push-interval", type=int)
    settings.add_argument("--quiet-start")
    settings.add_argument("--quiet-end")
    settings.add_argument("--daily-new-limit", type=int)
    settings.add_argument("--llm-cap", type=float)

    deck = sub.add_parser("deck")
    deck_sub = deck.add_subparsers(dest="action", required=True)
    deck_sub.add_parser("list")
    create = deck_sub.add_parser("create")
    create.add_argument("name")
    create.add_argument("--language", default="de")
    rename = deck_sub.add_parser("rename")
    rename.add_argument("deck_id", type=int)
    rename.add_argument("name")
    delete = deck_sub.add_parser("delete")
    delete.add_argument("deck_id", type=int)
    move = deck_sub.add_parser("move")
    move.add_argument("word_id", type=int)
    move.add_argument("deck_id", type=int)

    import_parser = sub.add_parser("import")
    import_parser.add_argument("path")
    import_parser.add_argument("--deck", required=True)
    import_parser.add_argument("--language", default="de")
    sub.add_parser("sync")
    export = sub.add_parser("export")
    export.add_argument("deck")
    export.add_argument("path")

    task = sub.add_parser("task")
    task.add_argument("--type", choices=TASK_TYPES)
    task.add_argument("--word")
    task.add_argument("--queue", choices=TASK_QUEUES, default="auto")
    task.add_argument("--deck")
    task.add_argument("--session-id")
    for name in ("task-new", "task-learning", "task-review"):
        queue_parser = sub.add_parser(name)
        queue_parser.add_argument("--type", choices=TASK_TYPES)
        queue_parser.add_argument("--word")
        queue_parser.add_argument("--deck")
        queue_parser.add_argument("--session-id")
    answer = sub.add_parser("answer")
    answer.add_argument("task_id")
    answer.add_argument("answer")
    due = sub.add_parser("due")
    due.add_argument("--limit", type=int, default=20)
    sub.add_parser("stats")
    word = sub.add_parser("word")
    word.add_argument("query")
    word.add_argument("--language")
    task_context = sub.add_parser("task-context")
    task_context.add_argument("task_id")
    history = sub.add_parser("history")
    history.add_argument("--limit", type=int, default=100)
    history.add_argument("--word")
    curator_run = sub.add_parser("curator-run")
    curator_run.add_argument("--kind", choices=["plan", "digest"], default="plan")
    curator_run.add_argument("--dry-run", action="store_true")

    session = sub.add_parser("session")
    session_sub = session.add_subparsers(dest="action", required=True)
    start = session_sub.add_parser("start")
    start.add_argument("--kind", choices=["micro", "long"], default="long")
    start.add_argument("--deck")
    start.add_argument("--target-count", type=int)
    session_sub.add_parser("stop")

    push = sub.add_parser("push")
    push_sub = push.add_subparsers(dest="action", required=True)
    for action in ("compose", "claim"):
        item = push_sub.add_parser(action)
        item.add_argument("--limit", type=int, default=5)

    push_plan = sub.add_parser("push-plan")
    push_plan_sub = push_plan.add_subparsers(dest="action", required=True)
    push_plan_sub.add_parser("get")
    set_plan = push_plan_sub.add_parser("set")
    set_plan.add_argument("json")

    propose = sub.add_parser("propose-words")
    propose.add_argument("--language", default="de")
    propose.add_argument("--deck", required=True)
    propose.add_argument("--cards-json", required=True)
    confirm = sub.add_parser("confirm-pending")
    confirm.add_argument("pending_id")
    confirm.add_argument("--move-existing", action="store_true")
    reject = sub.add_parser("reject-pending")
    reject.add_argument("pending_id")
    sub.add_parser("practice")
    return parser


def main() -> None:
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
