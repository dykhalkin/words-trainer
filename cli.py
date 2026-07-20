#!/usr/bin/env python3
"""JSON CLI over the same async PostgreSQL core used by the Telegram bot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from vocab import db, jobs, scheduler, statistics, storage, words

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("WORDS_DATA", ROOT / "data"))


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


async def command_word(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    query: int | str = int(args.query) if args.query.isdigit() else args.query
    async with database.connection() as conn:
        word = await db.fetch_one(
            conn,
            "SELECT * FROM words WHERE user_id = %s AND "
            + ("id = %s" if isinstance(query, int) else "lemma ILIKE %s")
            + (" AND language = %s" if args.language else "")
            + " ORDER BY id LIMIT 1",
            (
                user["id"],
                query if isinstance(query, int) else f"%{query}%",
                *([args.language] if args.language else []),
            ),
        )
        if word:
            word["progress"] = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (word["id"],)
            )
    if not word:
        raise LookupError(f"word not found: {args.query}")
    return word


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


async def command_disposition(database: db.Database, args: argparse.Namespace) -> Any:
    user = await require_user(database, args.user)
    query: int | str = int(args.word) if args.word.isdigit() else args.word
    if args.command == "word-archive":
        return await words.archive_word(database, user["id"], query)
    if args.command == "word-flag":
        return await words.flag_word(database, user["id"], query, reason=args.reason)
    if args.command == "word-restore":
        deck = await resolve_deck(database, user["id"], args.deck)
        return await words.restore_word(database, user["id"], query, deck["id"])
    return await words.replace_word_card(
        database, user["id"], int(args.word), json.loads(args.card_json)
    )


async def run(args: argparse.Namespace) -> int:
    database = db.Database(args.database_url)
    try:
        await database.open()
        if args.command == "migrate":
            await database.migrate()
            result: Any = {"migrated": True}
        elif args.command == "health":
            async with database.connection() as conn:
                row = await db.fetch_one(conn, "SELECT current_database() AS database, now() AS now")
            result = {"ok": True, **row}
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
        elif args.command == "stats":
            user = await require_user(database, args.user)
            deck = await resolve_deck(database, user["id"], args.deck)
            result = await statistics.stats(
                database,
                user["id"],
                deck_id=deck["id"] if deck else None,
                days=args.days,
            )
        elif args.command == "word":
            result = await command_word(database, args)
        elif args.command in {"word-archive", "word-restore", "word-flag", "word-fix"}:
            result = await command_disposition(database, args)
        elif args.command == "issues":
            user = await require_user(database, args.user)
            deck = await resolve_deck(database, user["id"], args.deck)
            result = {
                "issues": await words.list_word_issues(
                    database,
                    user["id"],
                    deck_id=deck["id"] if deck else None,
                    limit=args.limit,
                    offset=args.offset,
                )
            }
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
        elif args.command == "job":
            if args.action == "list":
                result = {"jobs": await jobs.list_jobs(database)}
            elif args.action == "runs":
                result = {
                    "runs": await jobs.list_runs(
                        database, job_name=args.name, limit=args.limit
                    )
                }
            elif args.action == "run":
                result = await jobs.enqueue_run(database, args.name, force=args.force)
            else:
                result = await jobs.set_enabled(
                    database, args.name, args.action == "enable"
                )
        elif args.command in {"propose-words", "confirm-pending", "reject-pending"}:
            result = await command_pending(database, args)
        else:
            raise ValueError(f"unsupported command: {args.command}")
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
    sub.add_parser("health")

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

    stats = sub.add_parser("stats")
    stats.add_argument("--deck")
    stats.add_argument("--days", type=int, default=7)
    word = sub.add_parser("word")
    word.add_argument("query")
    word.add_argument("--language")
    archive = sub.add_parser("word-archive")
    archive.add_argument("word")
    restore = sub.add_parser("word-restore")
    restore.add_argument("word")
    restore.add_argument("--deck", required=True)
    flag = sub.add_parser("word-flag")
    flag.add_argument("word")
    flag.add_argument("--reason")
    fix = sub.add_parser("word-fix")
    fix.add_argument("word")
    fix.add_argument("--card-json", required=True)
    issues = sub.add_parser("issues")
    issues.add_argument("--deck")
    issues.add_argument("--limit", type=int, default=100)
    issues.add_argument("--offset", type=int, default=0)
    history = sub.add_parser("history")
    history.add_argument("--limit", type=int, default=100)
    history.add_argument("--word")
    job = sub.add_parser("job")
    job_sub = job.add_subparsers(dest="action", required=True)
    job_sub.add_parser("list")
    for action in ("enable", "disable"):
        item = job_sub.add_parser(action)
        item.add_argument("name", choices=jobs.JOB_NAMES)
    run_job = job_sub.add_parser("run")
    run_job.add_argument("name", choices=jobs.JOB_NAMES)
    run_job.add_argument("--force", action="store_true")
    runs = job_sub.add_parser("runs")
    runs.add_argument("--name", choices=jobs.JOB_NAMES)
    runs.add_argument("--limit", type=int, default=50)

    propose = sub.add_parser("propose-words")
    propose.add_argument("--language", default="de")
    propose.add_argument("--deck", required=True)
    propose.add_argument("--cards-json", required=True)
    confirm = sub.add_parser("confirm-pending")
    confirm.add_argument("pending_id")
    confirm.add_argument("--move-existing", action="store_true")
    reject = sub.add_parser("reject-pending")
    reject.add_argument("pending_id")
    return parser


def main() -> None:
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
