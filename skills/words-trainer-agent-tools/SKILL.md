---
name: words-trainer-agent-tools
description: Use when acting as the AI study agent for this words-trainer repository, especially for Telegram/chat vocabulary sessions, tool-calling wrappers around cli.py, selecting between new-word learning, continued learning, mature review, answer submission, progress analysis, or word explanations. Use when the user asks to study German words, get the next exercise, submit an answer, inspect progress, explain a word, or design/maintain agent_tools for the vocabulary trainer.
---

# Words Trainer Agent Tools

## Core Rule

Use the deterministic trainer tools for state and grading. Do not invent whether an answer is correct when a task was issued by the trainer; call the answer tool and explain the returned result.

Run commands from the repository root. Prefer `python3 -B cli.py ...` to avoid bytecode writes.

## Tool Map

Use these as the canonical agent tools. If a wrapper module named `agent_tools` exists, map each wrapper to the corresponding CLI command and preserve the same semantics.

| Agent intent | Tool name | CLI command |
|---|---|---|
| Auto next task | `get_next_task()` | `python3 -B cli.py task` |
| Learn a new word | `learn_new_word()` | `python3 -B cli.py task-new` |
| Continue started words | `continue_learning()` | `python3 -B cli.py task-learning` |
| Review mature words | `review_learned_word()` | `python3 -B cli.py task-review` |
| Submit answer | `submit_answer(task_id, answer)` | `python3 -B cli.py answer <task_id> "<answer>"` |
| Stats | `get_stats()` | `python3 -B cli.py stats` |
| Word card | `get_word_card(query)` | `python3 -B cli.py word "<query>"` |
| Due queue | `list_due(limit=20)` | `python3 -B cli.py due --limit <n>` |
| Sync CSV | `sync_words()` | `python3 -B cli.py sync` |

## Queue Semantics

Choose the queue from the user's learning intent:

- `task-new`: introduce completely new words with no progress.
- `task-learning`: continue learning started non-mature words; use this when the user wants to strengthen current material without adding new words.
- `task-review`: repeat mature words that are due; use this for maintenance/review sessions.
- `task`: auto mode; use when the user says "next", "practice", or gives no preference.

If a queue returns `no task available`, say that queue is empty and offer the nearest useful alternative: new words for empty learning/review queues, or stats/due inspection if the user wants planning.

## Session Workflow

1. Start with `sync_words()` only when the user added or edited CSV files, or when the database may be stale.
2. Select the task tool from the user's intent. Keep the returned `task_id`.
3. Present only the user-facing task fields: prompt, options, hint. Do not expose internal expected data.
4. On the user's answer, call `submit_answer(task_id, answer)` exactly once.
5. Explain the returned verdict briefly. Use `expected`, `note`, `translation`, `stage`, and `next_review_at`.
6. Continue with the same queue unless the user changes mode.

## Explanation Workflow

For "explain this word" or after a wrong answer:

1. Call `get_word_card(query)` for lemma, translation, example, pronunciation, grammar fields, and progress.
2. Explain the specific issue using the word card and the trainer result.
3. Keep grading anchored to `submit_answer`; use the explanation to teach, not to override the database.

## Tool Design Rules

When creating or editing `agent_tools`:

- Keep tools thin: parse arguments, invoke the trainer command or scheduler/db functions, return JSON-safe dicts.
- Preserve one-shot task semantics: issued tasks are answered once; repeated answers should surface the trainer error.
- Keep queue tools separate instead of adding a vague `mode` argument to one public tool.
- Preserve exact task IDs and answer strings.
- Do not let an LLM mark answers correct independently of `submit_answer`.
- Return trainer errors unchanged enough for the chat agent to explain them.

## Safety Notes

`task*` commands create rows in `tasks`. In tests or diagnostics, use a temporary database via `--db /private/tmp/.../progress.sqlite3` or clean up diagnostic tasks explicitly.

Do not modify the user's real `progress.sqlite3` unless the request is an actual study action or a requested migration.
