---
name: words-trainer-agent-tools
description: Use when acting as or maintaining the learner-scoped AI tutor for this PostgreSQL Telegram vocabulary trainer.
---

# Words Trainer Agent Tools

## Invariants

- The deterministic scheduler alone grades answers and writes progress/reviews.
- Every call is scoped to the resolved learner; never accept a model-supplied user ID.
- An unanswered `task-context` must not contain `expected`.
- AI-created cards are strict `WordCard` proposals in `pending_cards`; only the
  learner's confirm callback commits them.
- CSV is import/export, not the source of truth.

Run from the repository root with `uv run python cli.py`. Put global options
before the subcommand, for example `--user spouse task`.

## Tool map

| Intent | Core/agent tool | Diagnostic CLI |
|---|---|---|
| Next exercise | `scheduler.create_task` | `task` |
| New / learning / review | queue argument | `task-new`, `task-learning`, `task-review` |
| Grade once | `scheduler.submit_answer` | `answer <task_id> <answer>` |
| Safe task context | `scheduler.task_context` | `task-context <task_id>` |
| Word card | `words.get_word` | `word <query>` |
| Stats/history/due | scheduler reads | `stats`, `history`, `due` |
| Decks | words lifecycle | `deck list/create/rename/delete/move` |
| Stage cards | `words.stage_cards` | `propose-words` |
| Resolve cards | commit/reject | `confirm-pending`, `reject-pending` |
| Curator plan | curator plan table | `push-plan get/set` |

Do not expose sync/import or deck mutation tools to the conversational model.
Those are owner/admin surfaces. The tutor's only write tool is staged proposal.

## Routing

Telegram routes command → callback → persisted pending typed task → tutor chat.
For an issued task show prompt/options/hint only, preserve its exact ID, call
`submit_answer` once, then explain the returned verdict without overriding it.

## Failure behavior

OpenAI failures or budget exhaustion disable chat, explanations, and curator
generation only. Deterministic drills, grading, sessions, and fallback push
composition continue to work.
