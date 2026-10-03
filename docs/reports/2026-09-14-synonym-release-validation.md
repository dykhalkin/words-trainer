> Исторический отчёт о прежней реализации. Актуальный результат: [переработка 2026-09-15](2026-09-15-synonym-rewrite.md).

# Fair Synonym Release Validation

Date: 2026-09-14

Scope: final narrow high validation of the fair-synonym tree after the prior reports, plus follow-up revalidation after the stale-retry fix completed. I read the plan and all existing reports, independently inspected the current code, ran the full suite, ran fresh temporary-DB probes, and in the first pass made four real configured model calls using `load_settings()` with synthetic nonprivate sentences only. I did not edit application code, touch production data, restart, deploy, or commit.

Verdict: locally validated from the fair-synonym release-validation perspective. Production rollout still requires the normal operational steps: apply migrations in the target environment, restart/deploy the live bot, and run a live Telegram smoke test.

## Resolved Blocker

### High: stale target retry could void and replace the newer active task after Continue

Original finding: `vocab/scheduler.py:1466` validated an answered retryable parent and the open session, but did not check whether a different open task already existed in that session. If the learner pressed `Дальше` after a retry offer, `continue_after_answer` could create a newer ordinary task; a late tap on the old `Попробовать с этим словом` callback then reached `start_target_retry`, which unconditionally voided all open user tasks before inserting the old retry.

Resolved behavior in the settled tree: `start_target_retry` now locks the user, returns an existing open retry idempotently, rejects a different current open task as `retry stale`, and rejects any session task created after the parent as `retry stale` before the retry insert/void step.

Original temp-DB repro:

1. Create reviewed `target_usage` for `verfügbar`.
2. Begin tutor evaluation with answer `Das Produkt ist wieder auf Lager.`
3. Finalize as `accepted`, `conditions_met=false`, `target_reproduced=false`, `defect_type=missing_required_target`; observed `retry_available=true`.
4. Simulate Continue by creating the next ordinary session task; observed newer task id `88bb8edc2fe6478a`, objective `introduction`, status `open`.
5. Call `scheduler.start_target_retry(parent_task_id)` from the stale retry button.

Observed before fix: the newer task became `voided`, and a new `target_retry=true` task became `open`. Expected: stale retry should return an error or the existing newer task without replacing it.

Follow-up revalidation:

- Stale retry after Continue: returned `{"error": "retry stale", "active_task_id": <newer task>}`; the newer task remained `open`; retry task count stayed `0`.
- Stale retry after the newer task was answered: returned `{"error": "retry stale", "active_task_id": <newer task>}`; the newer task remained `answered`; retry task count stayed `0`.
- Double-click retry: concurrent `start_target_retry` calls returned the same retry task; after neutral skip, a later retry returned `retry already used`; late finalization of the parent evaluation returned `stale=true`; reviews stayed `0`.

## Validated Paths

- Full suite: sandboxed `.venv/bin/python -m unittest discover -s tests` failed only because local Postgres on `127.0.0.1:55432` was blocked; reruns with local-DB access passed. Latest settled-tree run: `Ran 124 tests in 18.525s` / `OK`. The fake `test-key` OpenAI 401 curator fallback log was expected.
- `git diff --check`: passed with no whitespace errors.
- Context semantic validation: inspection shows candidate selection happens before generation, generator/validator budget reservations are their own DB operations, API runner calls happen outside task persistence, and `context.save_task` starts the insert transaction only after validation (`bot/context.py:118`, `bot/context.py:193`, `bot/context.py:266`, `vocab/context.py:131`). Persistence strips hidden required elements from `free_expression` snapshots (`vocab/context.py:157`).
- Bounded invalid context fallback: stubbed probe returned no task after 3 invalid generated outputs, made 3 generator and 3 validator calls, saved zero tasks, and recorded reconciled usage rows for every stubbed call.
- Live configured model samples: using `load_settings()`, temp DB, and no key output, I ran 2 real grader calls and 2 real context-validator calls on synthetic examples. The grader accepted target-usage meaning without the required visible word as `conditions_met=false`, `target_reproduced=false`, `missing_required_target`; accepted `auf Lager` for free expression; the validator rejected an inconsistent reference answer and accepted a valid free-expression task. All four `llm_usage` rows were `reconciled`.
- Ordinary automatic reviewed-pair progression: with no explicit `task_type` in next-task calls, automatic sessions introduced both reviewed peers, served target-usage practice, then progressed `verfügbar` through recognition and two recall reviews to reps `3`, stage `2`. Contrast did not preempt every future session.
- Exposure scope: unrelated disclosure did not neutralize a different word's recall (`neutral=false`, `assessed=true`). Same-session contrast exposure with options `["verfügbar", "zugänglich"]` made both same-word and peer recall neutral with `neutral_reason=target_shown`; a future-session peer recall progressed normally.
- Duplicate retry/skip/late grading: concurrent duplicate retry returned the same retry task; duplicate skip was idempotent; late finalization of the already finalized parent evaluation returned `stale=true`.
- Defective and assisted paths: inspected policy forces `ambiguous_task` / `grader_unavailable`, hints, disclosures, unmet conditions, and practice objectives to `neutral` before SRS application (`vocab/scheduler.py:730`, `vocab/scheduler.py:858`, `vocab/scheduler.py:894`).

## Scope Accuracy

The pilot scope is only the built-in reviewed material currently present in code: `verfügbar`, `zugänglich`, and `vorhanden`, with reviewed contrasts for `verfügbar` / `zugänglich` and `zugänglich` / `vorhanden`. Broader pilot candidates in `docs/reports/2026-09-14-synonym-pilot-validation.md` remain proposed material, not implemented release coverage.

The UX preview remains a renderer/static-flow artifact, not a live Telegram smoke test. The current release also still has no production migration run, production data audit/mutation, bot restart, deploy, or commit from this validation pass.

## Remaining Work

Before production rollout, run the normal operational migration/deploy/restart path and a live Telegram smoke test. Broader synonym material remains future work beyond the small reviewed pilot.
