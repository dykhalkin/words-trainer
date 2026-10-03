> Исторический отчёт о прежней реализации. Актуальный результат: [переработка 2026-09-15](2026-09-15-synonym-rewrite.md).

# Disclosure Buttons Fix Validation

Date: 2026-09-14

Scope: independent high review of the disclosure-button fix for the reported Telegram state where an ordinary `flashcard_ru_de` recall task for lemma `verruckteste` remained `open` with `answer_disclosed = true` after Show answer, but both rendered buttons behaved as inactive.

## Executive Result

No blocker found. The fixed code routes the disclosure keyboard through compact `FlowCallback` payloads (`f:<task>:retry` and `f:<task>:continue`) and `flow_action` now explicitly accepts an open task with `answer_disclosed = true` for both Retry and Continue. The retry path no longer depends on reviewed pilot material for ordinary disclosed recall cards; it creates a neutral practice-only retry from the disclosed parent when no reviewed `target_usage` material exists.

## Code Paths Reviewed

- `bot/keyboards.py`
  - `disclosure_keyboard()` now delegates to the same retry/continue keyboard family used after answered retry-available tasks.
  - The rendered callback payloads are compact `FlowCallback` values, so already-sent `f:<task>:retry` and `f:<task>:continue` payloads remain compatible.

- `bot/drill.py`
  - `hint_action(show)` records disclosure and sends the disclosure keyboard without manual callback construction.
  - `flow_action(continue)` accepts either an answered task or an open disclosed task. For an open disclosed task it calls `skip_current_task`, which finishes the task neutral and asks for the next task.
  - `flow_action(retry)` accepts answered, voided, or open disclosed tasks. It calls `start_target_retry`, removes markup only after success, and reports stale/unavailable retries with a callback toast.

- `vocab/scheduler.py`
  - `disclose_answer()` persists `answer_disclosed = true` and assistance level 4.
  - `start_target_retry()` distinguishes:
    - answered target-usage retry after `missing_required_target`;
    - open disclosed retry for the original user scenario.
  - Ordinary nonpilot disclosed recall falls back to `_disclosure_retry_practice()` and keeps SRS neutral.
  - Reviewed pilot material still prefers a generated `target_usage` retry.
  - Current/open-task and newer-session-task checks run before retry creation, preserving newer work and pending grading.
  - Session target count and quick-session expiry are checked before retry creation.
  - `skip_task()` discards pending evaluations and finalizes neutral without reviews or SRS updates.

## Validation Performed

### Rendered Disclosure Button Flow

Existing integration tests drive the actual UI path:

1. Create a real temporary PostgreSQL database.
2. Create a long session and a real `flashcard_ru_de` task.
3. Render the task with `_send_rendered_task()`.
4. Capture the rendered Show answer callback data.
5. Call `hint_action(show)`.
6. Capture the disclosure keyboard callback data for each rendered button.
7. `FlowCallback.unpack()` the captured payload.
8. Call `flow_action()` with fake Telegram `Message` and callback methods.

Covered cases:

- Ordinary nonpilot `flashcard_ru_de` disclosed retry for `verruckteste`.
  - Created exactly one retry task.
  - Retry prompt includes `Answer already shown` semantics in Russian text with the target.
  - Parent task becomes `voided`.

- Ordinary disclosed Continue for `verruckteste`.
  - Parent task becomes `answered`.
  - Verdict is neutral with `neutral_reason = skipped`.
  - Session counters become `answered_count = 1`, `assessed_count = 0`.
  - A next task is sent.

- Reviewed pilot disclosed retry for `verfugbar`.
  - Uses reviewed `target_usage` material.
  - Retry prompt asks to use `verfugbar`.
  - It does not fall back to the `Answer already shown` ordinary prompt.

- Duplicate retry callback.
  - Repeated Retry creates only one retry task.

- Stale retry callback after newer work.
  - Newer active work is preserved.
  - User receives `Повтор с этим словом уже недоступен`.

### Compatibility With Already-Sent Post-Answer Flow Callbacks

I also ran an ad-hoc handler validation against two separate real temporary DB fixtures for the original `post_answer_keyboard(..., retry_available=True)` callbacks:

- Captured `f:3d6bc6879e6e453b:retry`, unpacked it with `FlowCallback.unpack()`, then passed it to `drill.flow_action()`.
  - Result: one retry task created through the handler path.

- Captured `f:a11b5e89b10348ed:continue`, unpacked it with `FlowCallback.unpack()`, then passed it to `drill.flow_action()`.
  - Result: session retained the already answered neutral parent count and one next open task was created.

This confirms the fix did not strand already-sent `f:` callbacks from answered retry-available screens.

### Scheduler and Regression Coverage

Targeted tests also cover:

- Missing-target retry still works for answered `target_usage` tasks.
- Ordinary disclosed recall retry is practice-only and SRS-neutral.
- Reviewed pilot disclosed recall retry still uses reviewed target usage.
- Continue after disclosure skips once and blocks stale retry.
- Stale retry after newer pending grading preserves the newer open task and pending evaluation.
- Stale retry after newer answered work remains revoked.
- Quick-session expiry returns `session expired` without writing a review.
- Session limit blocks further retry after completion.
- Pending grading skip discards the pending evaluation and writes no review.
- Direct answer after disclosure remains neutral and writes no review.

## Commands Run

- `uv run python -m unittest tests.test_bot.BotDisclosureFlowIntegrationTests tests.test_bot.DrillFlowTests tests.test_sessions.SessionIntegrityTests.test_disclosed_ordinary_recall_can_start_neutral_retry tests.test_sessions.SessionIntegrityTests.test_disclosed_pilot_uses_reviewed_target_retry tests.test_sessions.SessionIntegrityTests.test_continue_after_disclosure_skips_once_and_blocks_stale_retry tests.test_sessions.SessionIntegrityTests.test_stale_target_retry_after_continue_preserves_newer_open_task tests.test_sessions.SessionIntegrityTests.test_stale_target_retry_after_answered_newer_task_stays_revoked tests.test_sessions.SessionIntegrityTests.test_target_usage_alternative_is_neutral_and_blocks_session_loop tests.test_sessions.SessionIntegrityTests.test_quick_session_applies_due_review_and_expires_after_five_minutes tests.test_sessions.SessionIntegrityTests.test_skip_discards_pending_grade_and_is_session_neutral tests.test_grading.TwoPhaseGradingTests.test_disclosed_answer_is_answered_neutrally_without_review`
  - Result: 18 tests passed.

- `uv run python -m unittest`
  - Result: 131 tests passed.
  - Note: suite logs include an expected invalid `test-key` OpenAI 401 in a curator-unavailable path, but the test run completed `OK`.

- Ad-hoc handler validation for old post-answer `f:` retry/continue callbacks.
  - Result: both callback paths passed against separate temporary databases.

- `git diff --check`
  - Result: clean.

## Diff Check

Current working tree remains broadly dirty from the feature/fix work under review. I intentionally modified only this report file.

Key modified production surfaces in the diff include:

- `bot/drill.py`
- `bot/keyboards.py`
- `vocab/scheduler.py`
- related exercise/grading/session support files

Key modified tests include:

- `tests/test_bot.py`
- `tests/test_sessions.py`
- `tests/test_grading.py`

## Residual Risk

No production database, deployment, restart, commit, or live Telegram send was performed. The validation uses fake Telegram `Message`/callback methods and isolated temporary PostgreSQL databases only.

The only residual risk is manual Telegram rendering not visually rechecked on a phone. Functionally, the callback data captured from rendered keyboards now reaches the intended handler branches for both buttons.
