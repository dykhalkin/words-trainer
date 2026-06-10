#!/usr/bin/env python3
"""Vocabulary trainer CLI.

Machine commands print JSON to stdout — designed to be called as agent tools:

    python3 cli.py sync                      # load/refresh words from CSV files
    python3 cli.py task [--type T] [--word W]  # create the next exercise
    python3 cli.py answer TASK_ID "ответ"      # grade an answer, update SRS
    python3 cli.py due                        # what is due for review
    python3 cli.py stats                      # learning progress overview
    python3 cli.py word LEMMA                 # full word card (for explanations)

Human command:

    python3 cli.py practice                   # interactive training loop
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from vocab import db, scheduler, storage

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("WORDS_DATA", ROOT / "data"))
TASK_TYPES = ["choice", "flashcard_de_ru", "flashcard_ru_de", "cloze", "grammar"]
TASK_QUEUES = ["auto", "new", "learning", "review"]


def out(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_sync(conn, args) -> None:
    if not DATA_DIR.is_dir():
        out({"error": f"data directory not found: {DATA_DIR}"})
        sys.exit(1)
    words = storage.load_dir(DATA_DIR)
    out(db.sync_words(conn, words))


def cmd_task(conn, args) -> None:
    task = scheduler.create_task(
        conn,
        word_query=args.word,
        task_type=args.type,
        queue=getattr(args, "queue", "auto"),
    )
    if task is None:
        out({"error": "no task available", "hint": "run 'sync' first or check --word/--type"})
        sys.exit(1)
    out(task)


def cmd_task_queue(conn, args) -> None:
    args.queue = args.command.removeprefix("task-")
    cmd_task(conn, args)


def cmd_answer(conn, args) -> None:
    result = scheduler.submit_answer(conn, args.task_id, args.answer)
    if result is None:
        out({"error": f"task not found: {args.task_id}"})
        sys.exit(1)
    out(result)


def cmd_due(conn, args) -> None:
    rows = db.fetch_due(conn, limit=args.limit)
    new = db.fetch_new(conn, limit=args.limit)
    out({
        "due": [
            {"lemma": r["lemma"], "kind": r["kind"], "due_at": r["due_at"], "reps": r["reps"]}
            for r in rows
        ],
        "new_available": len(new),
    })


def cmd_stats(conn, args) -> None:
    out(db.stats(conn))


def cmd_word(conn, args) -> None:
    word = db.find_word(conn, args.query)
    if word is None:
        out({"error": f"word not found: {args.query}"})
        sys.exit(1)
    data = dataclasses.asdict(word)
    prog = db.get_progress(conn, word.db_id)
    data["progress"] = dict(prog) if prog else None
    out(data)


def cmd_practice(conn, args) -> None:
    print("Тренировка. Пустой ввод или 'q' — выход.\n")
    while True:
        task = scheduler.create_task(conn)
        if task is None:
            print("Нет доступных заданий. Сначала выполни: python3 cli.py sync")
            return
        print(f"[{task['type']}] {task['prompt']}")
        for i, opt in enumerate(task.get("options", []), 1):
            print(f"  {i}. {opt}")
        if task.get("hint"):
            print(f"  подсказка: {task['hint']}")
        answer = input("> ").strip()
        if not answer or answer.lower() in ("q", "quit", "exit"):
            return
        result = scheduler.submit_answer(conn, task["task_id"], answer)
        mark = "✅" if result["correct"] else "❌"
        note = f" — {result['note']}" if result.get("note") else ""
        print(f"{mark} {result['expected']}{note}")
        print(f"   следующее повторение через {result['interval_days']} дн.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="German vocabulary trainer")
    parser.add_argument(
        "--db",
        default=os.environ.get("WORDS_DB", str(ROOT / "progress.sqlite3")),
        help="path to the SQLite progress database",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="import/refresh words from CSV files")

    p = sub.add_parser("task", help="create the next exercise (JSON)")
    p.add_argument("--type", choices=TASK_TYPES, help="force a specific exercise type")
    p.add_argument("--word", help="force a specific word (lemma or substring)")
    p.add_argument("--queue", choices=TASK_QUEUES, default="auto", help="select learning queue")

    for name, help_text in (
        ("task-new", "create a task for a never-studied word"),
        ("task-learning", "create a task for a started non-mature word"),
        ("task-review", "create a due review task for a mature word"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--type", choices=TASK_TYPES, help="force a specific exercise type")
        p.add_argument("--word", help="force a specific word (lemma or substring)")

    p = sub.add_parser("answer", help="grade an answer for a task")
    p.add_argument("task_id")
    p.add_argument("answer")

    p = sub.add_parser("due", help="list words due for review")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="progress overview")

    p = sub.add_parser("word", help="show a full word card")
    p.add_argument("query")

    sub.add_parser("practice", help="interactive practice session")

    args = parser.parse_args()
    conn = db.connect(Path(args.db))
    {
        "sync": cmd_sync,
        "task": cmd_task,
        "task-new": cmd_task_queue,
        "task-learning": cmd_task_queue,
        "task-review": cmd_task_queue,
        "answer": cmd_answer,
        "due": cmd_due,
        "stats": cmd_stats,
        "word": cmd_word,
        "practice": cmd_practice,
    }[args.command](conn, args)


if __name__ == "__main__":
    main()
