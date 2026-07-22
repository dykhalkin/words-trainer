---
name: words-trainer-agent-tools
description: Use when acting as or maintaining the learner-scoped AI tutor for this PostgreSQL Telegram vocabulary trainer.
---

# Words Trainer Agent Tools

## Invariants

- Every typed exercise requires the studied language; never ask for a written
  native-language translation.
- Core checks button answers and normalized exact variants locally. A dedicated,
  tool-free answer grader evaluates only free-text mismatches.
- The conversational tutor and answer grader are separate roles. Neither writes
  progress/reviews; only the transactional scheduler finalizes a schema-valid
  structured grader verdict.
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
| Begin answer | `scheduler.begin_answer_submission` |
| Finalize semantic verdict | `scheduler.finalize_tutor_evaluation` |
| Record grader failure | `scheduler.fail_tutor_evaluation` |
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
`begin_answer_submission` once, and explain a deterministic verdict without
overriding it. If it returns a pending evaluation, the bot—not the conversational
tutor—calls the dedicated grader outside the database transaction. Reveal the
canonical answer and update SRS only after core finalization.

## Failure behavior

OpenAI failures or budget exhaustion disable chat and explanations. Exact and
button answers still complete locally. A mismatched free-text answer remains
open without a review or progress change and offers retry, answer editing, or an
explicit learner override. Curator failures use validated due-only reminder
fallback inside the learner's policy.
