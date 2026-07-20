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

## Failure posture

- PostgreSQL unavailable: the bot drops grading and reports storage outage; it
  never guesses or queues progress in memory.
- OpenAI unavailable/over budget: tutor and curator fail closed; drills and
  deterministic push composition continue.
- Ambiguous Telegram send timeout: delivery remains claimed, preferring one
  missed reminder over a duplicate family notification.
- Spouse onboarding: add `SPOUSE_CHAT_ID`, rerun `user bootstrap`, and restart;
  all decks and progress remain independent.
