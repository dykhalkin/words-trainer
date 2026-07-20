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

Call learner-scoped Python core functions directly. Never shell out to `cli.py`
for training: the CLI is an administrative control plane and intentionally has
no task, answer, session, or push-delivery commands.

## Tool map

| Intent | Core/agent tool |
|---|---|
| Next exercise | `scheduler.create_task` |
| New / learning / review | `create_task` queue argument |
| Grade once | `scheduler.submit_answer` |
| Safe task context | `scheduler.task_context` |
| Word card | `words.get_word` |
| Stats | `statistics.stats` |
| Decks | `words.list_decks` |
| Stage cards | `words.stage_cards` |
| Resolve cards | `words.commit_pending` / `reject_pending` |

Do not expose sync/import, direct deck mutation, archive/repair administration,
or job controls to the conversational model. Those are owner/admin surfaces.
The tutor's only write tool is a staged proposal.

## Routing

Telegram routes command → callback → persisted pending typed task → tutor chat.
For an issued task show prompt/options/hint only, preserve its exact ID, call
`submit_answer` once, then explain the returned verdict without overriding it.

## Failure behavior

OpenAI failures or budget exhaustion disable chat, explanations, and curator
generation only. Deterministic drills, grading, sessions, and fallback push
composition continue to work.
