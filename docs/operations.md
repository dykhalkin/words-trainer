# Wordsbot operations

## Startup

1. Enable colima at login (`brew services start colima`) and verify
   `docker compose up -d` starts PostgreSQL.
2. Keep the Mac awake: `sudo pmset -a sleep 0`; auto-login is required for a
   LaunchAgent and must be checked in System Settings.
3. Run `scripts/install_launchd.sh`, then `scripts/ops_check.sh`.
4. Inspect with `launchctl print gui/$UID/com.wordsbot.bot` and logs in
   `~/Library/Logs/wordsbot/`.

## Backups

`com.wordsbot.backup` runs a custom-format `pg_dump` daily at 03:15 and retains
14 days in `~/Library/Mobile Documents/com~apple~CloudDocs/Wordsbot Backups`.
Override with `WORDSBOT_BACKUP_DIR` in the protected env file. Run
`scripts/backup_postgres.sh` manually after important imports.

## Restore drill

Stop the bot LaunchAgent, choose a non-empty dump, and run
`scripts/restore_postgres.sh BACKUP.dump`. Type the explicit confirmation. Then
run migrations, `cli.py stats`, and restart the bot. Test restores on a spare
database/container before relying on the backup schedule.

## Manager jobs

Inspect and control bot-owned jobs without executing Telegram or OpenAI work in
the CLI process:

```bash
uv run python cli.py job list
uv run python cli.py job disable weekly_digest
uv run python cli.py job run push
uv run python cli.py job runs --name push
```

Run-now requests remain in PostgreSQL until the bot claims them. Use `--force`
to enqueue a disabled job. After deploying schema changes, create a backup,
run `cli.py migrate`, restart the bot, then verify `cli.py health`, `job list`,
and `scripts/ops_check.sh`.

Migration 004 backfills old review rows from each word's deck at migration
time. That is an approximation; new reviews retain the exact answer-time deck.

Migration 006 voids open legacy `flashcard_de_ru` tasks, adds the two-phase
answer-evaluation audit trail, translates existing quiet hours into a `smart`
reminder policy, and adds persistent reminder plans/deliveries. Existing users
with equal quiet-hour bounds migrate to `off`; newly bootstrapped users also
start `off` and must opt in through `/reminders`.

Migration 008 is a forward-only compatibility repair for databases that had an
earlier draft of migration 006 applied. It completes the evaluation snapshot,
reminder refresh queue, pending-action constraint, and curator generation index;
fresh databases receive the same final schema through migrations 006–008.

## OpenAI roles and pricing

Tutor, answer grader, and curator are separate roles. Configure the actual
rollout models with `TUTOR_MODEL`, optional `GRADER_MODEL`, and `CURATOR_MODEL`.
Set the matching role-specific input/output prices in the protected env file;
the built-in model-aware defaults currently cover `gpt-5.6-luna` ($1/$6) and
`gpt-5.6-terra` ($2.50/$15) per million input/output tokens, while generic
`LLM_*_USD_PER_MILLION` values remain the fallback for unknown models. After
changing a model or price, restart the bot and verify reconciled `llm_usage` rows. The
grader has no tools or chat history and never writes reviews or progress; core
does so only after validating its structured verdict.

## Reminder delivery

`job_controls` enables or disables global infrastructure only. `/reminders`
mutates a learner-owned `reminder_policies` row and enqueues a refresh request;
the live bot is the sole OpenAI and Telegram executor. A policy change cancels
all unclaimed deliveries from the old revision. A manual replan preserves the
15-minute freeze window. At claim time the bot rechecks policy revision, local
window, execution grace, minimum gap, active long sessions, recent practice,
and due words.

Useful read-only diagnostics:

```sql
-- Answers waiting for or failed by the semantic grader.
SELECT e.id, e.user_id, e.task_id, e.status, e.model, e.error,
       e.created_at, e.finished_at
FROM answer_evaluations e
WHERE e.status IN ('pending', 'failed')
ORDER BY e.created_at DESC;

-- Reviews and their deterministic/tutor/learner provenance.
SELECT id, user_id, task_id, quality, grading_source,
       answer_evaluation_id, grader_feedback, created_at
FROM reviews
ORDER BY created_at DESC
LIMIT 100;

-- Current learner policy and outstanding curator refreshes.
SELECT rp.*, rr.requested_revision, rr.requested_generation, rr.requested_at
FROM reminder_policies rp
LEFT JOIN reminder_refresh_requests rr USING (user_id)
ORDER BY rp.user_id;

-- Planned, claimed, sent, skipped, cancelled, or failed reminders.
SELECT id, user_id, status, source, reminder_revision, scheduled_for,
       claimed_at, sent_at, skip_reason, error
FROM deliveries
WHERE kind = 'push'
ORDER BY coalesce(scheduled_for, claimed_at) DESC
LIMIT 200;
```

## Failure posture

- PostgreSQL unavailable: the bot drops grading and reports storage outage; it
  never guesses or queues progress in memory.
- OpenAI unavailable/over budget: tutor chat fails closed; a free-text mismatch
  remains an open task with no review/SRS mutation and exposes retry/mark-wrong
  controls; curator scheduling falls back to validated due-only planning.
- Ambiguous Telegram send timeout: delivery remains claimed, preferring one
  missed reminder over a duplicate family notification.
- Spouse onboarding: add `SPOUSE_CHAT_ID`, rerun `user bootstrap`, and restart;
  all decks and progress remain independent.
