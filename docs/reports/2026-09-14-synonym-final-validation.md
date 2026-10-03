> Исторический отчёт о прежней реализации. Актуальный результат: [переработка 2026-09-15](2026-09-15-synonym-rewrite.md).

# Fair Synonym Final Validation

Date: 2026-09-14

Scope: final independent review of the corrected fair-synonym implementation after the core and Telegram fixes. I read the plan, prior code review, pilot report, UX preview, and current diff; checked the Orca inbox at the start; edited only this report.

Verdict: superseded by `docs/reports/2026-09-14-synonym-release-validation.md`. The later narrow high validation first found a stale target-retry blocker, then revalidated the completed fix and now records the tree as locally validated from the fair-synonym release-validation perspective.

## Test Results

- Initial sandboxed run of `.venv/bin/python -m unittest discover -s tests` failed because localhost Postgres access was blocked by the sandbox.
- Final required run, with escalation for temporary local Postgres DBs: `.venv/bin/python -m unittest discover -s tests`
- Result: `Ran 115 tests in 15.487s` / `OK`.
- Expected log observed: fake `test-key` OpenAI 401 from the curator fallback path.

I also ran an independent temporary-DB probe at `/private/tmp/synonym_validation_probe.py` against fresh Postgres databases. It exercised reviewed material exposure, target retry, hint/disclosure, skipped pending evaluations, defective verdicts, session denominator accounting, target-usage bare lemma handling, contrast eligibility, context hidden-target stripping, and choice-task schema state.

## Blocker Recheck

Resolved: ordinary `choice` and `choice_ru_de` paths no longer deterministically punish valid alternatives. Random recognition choices are marked unverified and routed to tutor-on-mismatch with an answer language and provenance (`vocab/exercises/choice.py:56`, `vocab/exercises/choice_ru_de.py:101`, `vocab/scheduler.py:1091`). The reverse RU→DE generator also filters exact duplicate/broadly overlapping translation keys and reviewed related senses before offering distractors (`vocab/exercises/choice_ru_de.py:40`).

Resolved: defective/ambiguous verdicts cannot write SRS reviews. `defect_type in DEFECT_TYPES` forces unassessed neutral policy (`vocab/scheduler.py:717`) and `_commit_review` skips `reviews`/`progress` when neutral (`vocab/scheduler.py:857`, `vocab/scheduler.py:935`). Independent repro: rejected `ambiguous_task` ended as `neutral_reason=defective`, `assessed=false`, zero reviews/progress.

Resolved: `free_expression` snapshots and context tasks do not persist hidden required targets. Generated context output is saved with `required_elements=[]` and `visible_constraints.hidden_target=false` regardless of model-provided required elements (`vocab/context.py:141`, `vocab/context.py:153`). Independent repro supplied generated `required_elements=["verfügbar"]`; persisted task had an empty list.

Resolved: voluntary target retry and neutral skip are real backend and Telegram paths. Retry/continue buttons are rendered (`bot/keyboards.py:120`, `bot/keyboards.py:260`), Telegram dispatches retry/skip/continue (`bot/drill.py:431`, `bot/drill.py:464`), and `start_target_retry` enforces answered parent, open session, session slot, and one retry per word/session (`vocab/scheduler.py:1429`). Independent repro: duplicate retry clicks returned the same task, retry prompt showed the target, and a second retry after completion returned `retry already used`.

## Required Path Coverage

1. Reviewed-card cold start and later sessions: intro and target usage were neutral; same-session recall after exposure was neutral with `target_shown`; future-session explicit recall wrote one review and progressed to stage 1. This prevents fake independent recall without stalling the card forever.

2. Exposure in intro/usage/contrast/options/feedback: prior exposure blocks same-session recall through `_commit_review` checks for intro/target_usage/contrast and contrast options (`vocab/scheduler.py:823`). Future sessions can progress. Feedback labels accepted alternatives as “Ещё один вариант” and missing target as “Смысл передан” (`bot/presentation.py:96`).

3. Missing target retry/continue: missing target creates `retry_available`; retry is practice-only; duplicate clicks are idempotent; closed/limited sessions return errors; starting retry voids any open task and discards pending evaluations (`vocab/scheduler.py:1453`, `vocab/scheduler.py:1517`).

4. Pending grading races: skip discards pending evaluations and late finalize returns stale (`vocab/scheduler.py:1370`). Disclosure/hints mark assistance and make later answers neutral (`vocab/scheduler.py:723`, `vocab/scheduler.py:1660`). Learner “mark wrong” remains explicit; for unverified choice/grader-unavailable it is neutral-defective rather than a false lapse.

5. Session denominator: independent repro with three completed tasks, one defective neutral, produced `answered_count=3`, `assessed_count=2`, `correct_count=1`. Intro/practice-only events are not counted as correctness.

6. Empty/exhausted hints: no-content hint returned level 0 and no progress/review; answer disclosure was neutral with zero reviews.

7. Target usage sentence requirement: bare `zugänglich` did not fast-pass locally; it went to tutor evaluation instead of receiving deterministic credit (`vocab/exercises/fair.py:127`).

8. Contrast eligibility: cold contrast for a related but unseen peer returned no task; after introducing the related sense, contrast generated with `material_version=fair-synonym-pilot-v1`, scoped options, and reviewed accepted set (`vocab/exercises/fair.py:198`).

9. Card correction/import: tasks snapshot `card_snapshot` at creation (`vocab/scheduler.py:601`), corrections audit original/corrected cards (`vocab/words.py:636`), and import refuses to overwrite DB-edited cards without an import baseline match (`vocab/storage.py:231`). Existing open-task grading meaning is therefore versioned by task snapshot rather than silently changing with later card edits.

10. Ordinary/non-pilot cards and `/quick`: full suite covers ordinary training and quick rotation. `/quick` remains choice/choice_ru_de only via `_quick_task_type`; unverified choice mismatches route to tutor rather than hard-failing valid visible alternatives.

11. Preflight/fallback and transactions: generated tasks are preflighted before insert (`vocab/scheduler.py:391`); choice/target/free/contrast contracts are validated. LLM generation/grading reserves budget, performs API work outside the DB persistence transaction, then saves/reconciles afterward (`bot/context.py:90`, `vocab/context.py:162`).

12. UX preview consistency: current keyboard and presentation code match the UX preview: retry/continue, skip, hint, disclosure, grading retry/override, and contrast/help actions are reachable. Alternative feedback does not imply hidden help; neutral text says progress did not change.

## Remaining Limits

No live Telegram messages, production DB writes, bot restart, deploy, or commit were performed. I did not run a live real-LLM grader/generator call because the current blockers were better covered by deterministic DB repros and the suite; the existing fake-key 401 path remains expected. Optional future work: expand reviewed pilot material beyond the current small set, add a manual Telegram smoke checklist, and add an operational migration check that reports the final `ck_tasks_answer_language` definition after deployment.
