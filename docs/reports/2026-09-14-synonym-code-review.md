> Исторический отчёт о прежней реализации. Актуальный результат: [переработка 2026-09-15](2026-09-15-synonym-rewrite.md).

# Fair Synonym Practice Code Review

Date: 2026-09-14

Scope: independent read-only review of the fair synonym implementation against `docs/plans/2026-09-14-001-feat-fair-synonym-practice-plan.md`, `docs/reports/2026-09-14-synonym-pilot-validation.md`, and the current working-tree diff. I modified only this report.

Verdict: not ready. The implementation covers a substantial part of the contract, but there are concrete blockers around valid choice alternatives and neutral defective-task handling, plus a missing voluntary retry path.

## Findings

### High: `choice_ru_de` still rejects a visible valid alternative and writes a failed SRS review

Contract violated: "Two acceptable button answers do not create a false error"; button tasks must keep all valid choices in the task snapshot; an ambiguous Russian cue such as `доступный` must not force hidden `zugänglich` over valid `verfügbar`.

Code: `vocab/exercises/choice_ru_de.py:49-62` builds options from random distractors plus the target, then stores only `accepted: [visible_word]`. The checker can accept multiple values if present, but generation never populates those alternatives.

Repro run against a temporary test DB:

```text
Cards: zugänglich / verfügbar, both with translation "доступный".
Task: task_type="choice_ru_de" for zugänglich.
Prompt/options: "Как по-немецки «доступный»?", ["zugänglich", "verfügbar"].
Answer: "verfügbar".
Observed: correct=False, review={correct: False, quality: 1}, progress={reps: 0, lapses: 1, interval_days: 0.0}.
Expected: no false error; either both choices accepted for the broad cue or the unsafe task is not generated.
```

This is not limited to the new contrast generator. The ordinary recognition path can still punish the learner for a valid visible answer before the semantic grader has any chance to intervene because the button path is deterministic.

### High: ambiguous or defective grader outcomes can still punish the learner

Contract violated: defective or contradictory tasks must finish neutral and must not write `reviews` or mutate `progress`.

Code: `vocab/scheduler.py:1037-1048` carries `defect_type` through but only switches to neutral for assistance, disclosure, educational objectives, context sessions, or accepted non-target alternatives. A rejected tutor verdict with `defect_type="ambiguous_task"` remains `srs_action="review"`, and `_commit_review` then applies SRS at `vocab/scheduler.py:686-710`.

Repro run against a temporary test DB:

```text
Task: flashcard_ru_de for zugänglich, prompt "Скажи по-немецки: «доступный»".
Answer: "verfügbar".
Finalize tutor evaluation with decision="rejected", conditions_met=False, target_reproduced=False, defect_type="ambiguous_task".
Observed: neutral=False, srs_action="review", review_count=1, progress={reps: 0, lapses: 1, interval_days: 0.0}.
Expected: neutral task outcome, no review row, no progress change, because the defect is in the prompt contract rather than the learner answer.
```

The grader prompt says "do not punish the learner" for ambiguous tasks, but the persistence layer does not enforce that invariant. The fix should not depend on the model choosing an accepted decision for defective prompts.

### Medium: missing target retry is not actually offered or executable in the Telegram flow

Contract violated: when `target_usage` meaning is correct but the visible required word was not used, the bot should offer one voluntary "try with this word" follow-up without double review or penalty.

Code: `bot/keyboards.py:220-252` renders only YouGlish, Explain, Contrast, Archive, and Error after every answer. `bot/drill.py:220-230` immediately sends the next task after rendering the verdict. The neutral session blocker at `vocab/scheduler.py:740-748` then suppresses that word in-session when `target_reproduced=False`, so the promised voluntary follow-up has no reachable UI path.

Observed in code and scheduler behavior: a target-usage answer accepted by the tutor with `target_reproduced=False` becomes neutral and blocks the card for the rest of the session. Expected behavior is a specific follow-up action that creates one practice-only target retry and still prevents duplicate SRS credit.

### Medium: `context_llm` can persist hidden required elements despite `free_expression`

Contract violated: `free_expression` must never hide a required lemma, and the grader contract should be derived from visible prompt constraints rather than hidden reference semantics.

Code: `vocab/context.py:141-157` always stores `visible_constraints={"hidden_target": False}` but copies `generated["required_elements"]` into server-side `expected` without verifying those elements are visible in the prompt. `bot/grader.py:38-41` later tells the grader to enforce `expected.required_elements` for `context_llm`.

Repro run against a temporary test DB:

```text
Saved context task:
  objective="free_expression"
  visible_constraints={"hidden_target": False}
  prompt does not contain "zugänglich"
  expected.required_elements=["zugänglich"]
Observed pending grader context includes that hidden required element.
Expected: reject/regenerate such output or strip hidden required_elements before persisting the task.
```

A bounded real-grader probe did ignore the hidden element because the objective said `free_expression`, but the stored task snapshot is internally contradictory and relies on prompt obedience rather than enforcing the application contract.

## Positive Validation

- Complete suite passed with the requested command: `.venv/bin/python -m unittest discover -s tests`; result was 101 tests OK. The expected `test-key` 401 from the fallback grader test appeared.
- Orca runtime lifecycle was checked normally with `orca status --json`; runtime was ready and connected.
- Bounded real grader evaluation with nonprivate sentences passed the core semantic expectations: accepted `Das Produkt ist wieder auf Lager.` for free expression, accepted meaning-but-missing-target for target usage with `conditions_met=false` and `target_reproduced=false`, and rejected `zugänglich` for available appointments with `wrong_meaning`.
- Cold-start reviewed material path was exercised in a temporary DB: introduction was neutral, target usage was neutral, then ordinary choice recognition could create progress.
- Contrast readiness was exercised in a temporary DB: after the target had progress and the related meaning had been introduced, a `contrast` task became reachable.
- Hint/disclosure state is stored on the task, locked during updates, and used during deterministic and tutor finalization; simultaneous hint/answer races appear protected by the task row lock.
- Current context sessions remain practice-only at SRS time, and ordinary non-pilot cards still train through the existing generators.
- Teaching metadata is sense-scoped in the built-in pilot by exact lemma plus example; import/export round-trip has unit coverage, and German noun display still preserves article forms in the inspected paths.

## Limits

No production database migration, production writes, live Telegram messages, bot restart, deploy, or commit were performed. I did not run a manual Telegram walkthrough, so UI sequencing beyond static handler inspection remains unverified.
