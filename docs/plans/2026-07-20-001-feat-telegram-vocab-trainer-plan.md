---
title: Telegram Vocabulary Trainer Bot - Plan
type: feat
date: 2026-07-20
topic: telegram-vocab-trainer
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
deepened: 2026-07-20
---

# Telegram Vocabulary Trainer Bot - Plan

## Goal Capsule

- **Objective:** turn the existing German vocabulary trainer core (`vocab/`, `cli.py`) into a family Telegram bot (owner + spouse) with three learning modes and an AI agent, running 24/7 on the owner's Mac mini with PostgreSQL storage.
- **Product authority:** the Product Contract in this document; decisions are marked session-settled from the 2026-07-19/20 dialogue.
- **Execution profile:** four phases — core refactor, deterministic bot loop, chat agent, curator + ops. The deterministic loop is a fully usable product before any LLM code lands.
- **Stop conditions:** surface a genuine blocker (contradicts a session-settled decision or changes product scope) instead of guessing; everything else is implementer judgment.
- **Open blockers:** none.

---

## Product Contract

### Summary

A private family Telegram bot for daily vocabulary study, serving two learners (owner and spouse) with fully independent progress: proactive micro-sessions driven by each learner's spaced-repetition schedule, long training sessions on demand, free-form chat with an OpenAI agent (explanations and creating new words from conversation), and a mandatory curator agent that analyzes progress on a schedule and composes push content per learner. The trainer core is upgraded to an Anki-like deck model on PostgreSQL: the database is the single store, CSV is an import/export format, and the data model carries users and languages from day one (German exercise generators ship in v1; new languages arrive as decks plus generator sets).

### Problem Frame

The trainer core (simplified SM-2, 4 exercise types, new/learning/review queues, a JSON CLI that doubles as an agent tool surface) is already built and works in the terminal. But the terminal does not solve the owner's actual problem: consistency. Study must be initiated by the system, happen on the phone in spare 2–3 minute windows, and not depend on the discipline to sit down and practice. Off-the-shelf tools (including Anki-likes) did not cover the combination the owner needs — SRS drills plus a live AI explainer plus vocabulary growth straight from conversation — so the owner is building their own system for the family and wants all needs covered at once, without an intermediate MVP.

### Key Decisions

- **Full scope in v1, no MVP cut.** All three session modes and the curator ship in the first version. (session-settled: user-directed — chosen over a minimal first version: the owner has been through many tools and wants every need covered at once; the curator was explicitly pinned as non-severable.)
- **Multi-user and multi-language in the data model from day one; the spouse joins in v1.** Every progress-bearing table is per-user; decks carry a language. New languages are enabled by adding decks and exercise-generator sets — German ships in v1. (session-settled: user-directed — chosen over a single-user v1 and over schema-only readiness: retrofitting user identity into live progress data is the most expensive possible follow-up.)
- **PostgreSQL (in a colima container) is the single store; CSV is import/export only.** One CSV file = one deck on import. (session-settled: user-directed — chosen over SQLite+WAL, which remains technically sufficient at this scale: multi-user consistency without file-lock tuning, the owner's familiar professional stack, `pg_dump` backups, and colima already running on the Mac mini.)
- **Anki-like decks (topics).** Words are grouped into decks; each deck belongs to one learner and one language; a general deck per learner holds words without a topic; chat-created words go into topic decks. (session-settled: user-directed.)
- **SRS × decks — Anki-like hybrid.** Reminders and micro-sessions draw from the learner's global due queue across all their decks; a manual session can be scoped to one deck. (session-settled: user-approved — chosen over "always one global queue" and "session always per-deck".)
- **Exercise difficulty follows the word's stage in every mode.** Micro-sessions may require typed input; buttons are only for the basic memorization stage. (session-settled: user-directed — chosen over button-only micro-sessions: recognition-only checks on mature words distort the SM-2 rating.)
- **"Two loops + curator" architecture.** A deterministic loop runs drills and reminders with no LLM in the cycle; the agent joins in chat mode and on explicit "explain" requests; the curator is a scheduled background agent role. (session-settled: user-approved — chosen over "agent-in-the-middle": per-click latency and cost would hurt the most frequent scenario.)
- **LLM provider — OpenAI.** (session-settled: user-directed — chosen over Anthropic and "decide during planning".)
- **Hosting — the owner's Mac mini, 24/7.** (session-settled: user-directed — chosen over a VPS and "local laptop for now".)
- **Answer grading is deterministic.** The agent explains the trainer's verdict but never overrides it. (session-settled: user-approved — pinned earlier in the session and in `skills/words-trainer-agent-tools/SKILL.md`; chosen over LLM semantic grading: reproducible progress matters more than flexibility.)
- **SRS and exercises are inherited from the existing core.** Simplified SM-2 with stages 0–3, 4 exercise types (multiple choice, flashcards, cloze, grammar), new/learning/review queues. (session-settled: user-directed — chosen earlier in the session over FSRS/Leitner and other exercise sets.)

Storage model shift:

```mermaid
flowchart TB
    subgraph before [Before]
        CSV1[CSV files = source of truth] -->|one-way sync| DB1[(SQLite: progress only)]
    end
    subgraph after [After]
        CSVIMP[CSV import] -->|creates a deck for a learner| DB2[(PostgreSQL: users + decks + words + progress)]
        CHAT[Words from chat] -->|into topic deck| DB2
        DB2 -->|deck export| CSVEXP[CSV export]
    end
```

### Actors

- A1. **Learners** — the owner and their spouse; each with their own decks, progress, pushes, sessions, and digests. The owner additionally administers the system.
- A2. **Trainer** — the deterministic core: task generation, grading, SM-2 scheduling, storage.
- A3. **Chat agent** — an OpenAI agent with tool access to the trainer, always acting on behalf of one learner: explanations, topic teaching, word creation.
- A4. **Curator** — the same agent in a scheduled background role, run per learner: progress analysis, push composition, digests.

### Requirements

**Sessions and learning**

- R1. The bot initiates micro-sessions per learner: a push fires when that learner's global due queue has overdue words; a session is 3–5 exercises (fewer when fewer are due — no padding with new words unless the curator plan says so).
- R2. Exercises in every mode match the word's stage. Stage 0 uses recognition-only button choice; deeper stages use the stage-appropriate generators, which may need typed input (article/rection sub-drills stay button-based by design).
- R3. A learner can start a long session at any moment; it continues until they stop and can be scoped to a single deck (deck due words first, then the deck's new words, then the session ends with an explicit "nothing left to study in this deck" message when it starts empty).
- R4. After a trainer verdict the learner can request an explanation; the chat agent receives the task context — payload, the learner's answer, the verdict, the word card. `expected` is never exposed through task surfaces before grading.
- R5. In chat mode the agent explains grammar and words, teaches topics, and offers to create exercises/memos for the topic under discussion.

**Decks, languages, and vocabulary**

- R6. Words are grouped into decks; a deck belongs to one learner and one language; a general deck per learner holds words without a topic; learners create topics. The agent may propose a new deck; it is created only on learner confirmation.
- R7. CSV import creates or extends one of the importing learner's decks (one file = one deck); re-import is idempotent and never resets progress. When CSV content conflicts with a card modified in the DB after import, the DB wins and the import summary reports the skipped conflict.
- R8. Any deck can be exported to CSV in the same row formats the import accepts.
- R9. Agent-created words from chat get a full card (translation, example sentence, pronunciation; conjugation for verbs; article and plural for nouns) and join the learner's new-word queue inside their topic deck (scheduled by SRS from the first review).
- R10. A lemma is unique within one learner's vocabulary per language; an import or confirmed agent card matching an existing lemma reuses the existing word and its progress (optionally moving it to the topic deck) and reports the collision — progress never forks.
- R11. Exercise generators are registered per language; the German set ships in v1. A deck whose language has no grammar/cloze generators falls back to the language-agnostic types (flashcards, choice).

**Reminders and curator**

- R12. Reminders work per learner off their global due queue; an empty queue sends no push; pushes are suppressed while that learner has an open long session.
- R13. The curator analyzes each learner's data on a schedule and composes their upcoming micro-session content, including drill series that target recurring error patterns.
- R14. The curator sends periodic per-learner progress digests with recommendations.

**Constraints**

- R15. The bot is private: it serves only allowlisted chat_ids (owner and spouse); all other chat_ids are ignored. Learners see only their own data.
- R16. Only the trainer grades answers and writes progress; the agent cannot mark an answer correct around it.
- R17. Drills (R1–R3) run without LLM calls; OpenAI downtime never blocks studying.

**Session and data integrity**

- R18. One active task per learner: issuing a new task voids the previous unanswered one; interactions with voided or expired tasks get an "exercise expired" response and never write progress.
- R19. Tasks expire: an unanswered task is voided after 24 hours or when a newer task is issued for the same word.
- R20. The learner's raw answer text is persisted with every review (feeds R4 explanations and R13 error-pattern analysis).
- R21. Agent-created cards are staged: the bot shows a preview and inserts only on explicit learner confirmation; structural validation (verb = full 3-tense × 6-person conjugation, noun = article + plural, for languages whose validators exist) rejects partial cards before insert.
- R22. Deck lifecycle: rename is metadata-only; deleting a deck moves its words to the learner's general deck with progress intact; moving a word between decks keeps its progress.
- R23. Pushes respect a per-learner minimum interval and quiet hours; a lapse retry (the 10-minute re-queue after a wrong answer) alone never triggers a push.
- R24. Push composition treats the curator plan as an optional overlay: with no fresh plan (first run, failed run, OpenAI outage), pushes fire with the deterministic due-queue composition.

### Key Flows

- F1. Push micro-session
  - **Trigger:** the scheduler finds overdue words in a learner's due queue, the minimum push interval has passed, quiet hours are over, and no long session is open; composition comes from that learner's fresh curator plan when one exists, deterministic order otherwise (R24).
  - **Actors:** A2 → A1.
  - **Steps:** the bot sends the first exercise; the learner answers (button or text); the trainer grades and shows the verdict; after 3–5 exercises a short session summary follows; on a wrong answer an "explain" affordance is available (→ F3 context).
  - **Covers:** R1, R2, R12, R16, R17, R23, R24.
- F2. Long session
  - **Trigger:** learner command/button, optionally with a deck choice.
  - **Steps:** an unbounded "exercise → answer → verdict" loop; exits on command; summary at the end; an empty deck-scoped start yields one "nothing to study here right now" message.
  - **Covers:** R2, R3, R16, R17.
- F3. Agent chat and word creation
  - **Trigger:** a free-form learner message with no pending typed-answer task, or the "explain" button after a verdict.
  - **Steps:** the agent replies with tool access scoped to that learner (word card, stats, task context); when teaching a new topic it proposes word cards; the bot renders a staged preview; on confirmation the cards land in the topic deck and join the new-word queue.
  - **Covers:** R4, R5, R6, R9, R10, R16, R21.
- F4. Curator cycle
  - **Trigger:** schedule (daily plan run, weekly digest run), executed per learner.
  - **Steps:** deterministic Python analysis of that learner's stats and answer history; one structured LLM call composes the push plan (and digest on digest runs); the plan is stored; the push executor re-validates plan items against the live due queue at send time.
  - **Covers:** R13, R14, R24.

### Acceptance Examples

- AE1. **Covers R2, R16.** Given a mature word (stage 3) in a micro-session, when it comes up in a push, then the exercise is productive (cloze/grammar), not a stage-0 recognition choice; the trainer issues the verdict.
- AE2. **Covers R9, R21.** Given a learner asked in chat to study the topic "visiting a doctor", when the agent proposes words and the learner confirms the preview, then each word has a full validated card, lives in the topic's deck, and joins that learner's new-word queue (reachable via deck-scoped sessions and curator plans).
- AE3. **Covers R1, R12.** Given every word is reviewed and the learner's due queue is empty, when push time arrives, then no push is sent to that learner.
- AE4. **Covers R7.** Given a deck imported from CSV with progress on its words, when the same CSV is imported again, then no duplicates are created and progress is preserved.
- AE5. **Covers R15.** Given a message from a chat_id not on the allowlist, when the bot receives it, then the bot does not reply and writes nothing to the database.
- AE6. **Covers R17, R24.** Given the OpenAI API is down and the last curator run failed, when push time arrives and a learner runs sessions, then pushes and drills work normally with deterministic composition; only explanations and chat are unavailable.
- AE7. **Covers R18, R19.** Given an exercise message from three days ago whose task was voided, when the learner taps its button, then the bot answers with an ephemeral "exercise expired" notice and no progress changes.
- AE8. **Covers R18.** Given a pending typed-answer task, when the learner sends free text, then it is graded as the answer to that task; given no pending task, the same text goes to the chat agent.
- AE9. **Covers R21.** Given the agent generated a verb card with an incomplete conjugation table, when validation runs, then the card is rejected and re-requested; it never reaches the words table.
- AE10. **Covers R15.** Given both learners study in the same hour, when either requests stats or receives a push, then each sees only their own decks, progress, and history.

### Success Criteria

- Both learners study regularly: most pushes end in a completed micro-session rather than being ignored (accepted baseline; refined from real usage).
- All three modes and the curator are reachable from the phone with no terminal involved.

### Scope Boundaries

- Out of scope: users beyond the two allowlisted learners, voice interfaces, Telegram Mini App / web UI, learner-facing UI languages other than Russian.
- v1 ships exercise generators for German only; adding a language is a follow-up increment (decks + generator set), not a v1 deliverable.
- The trainer core changes only as far as the deck/user/language model, session integrity, and agent word creation require; the SRS algorithm and exercise types are not revisited.

### Deferred to Follow-Up Work

- Second-language generator sets (the registry and fallback ship in v1).
- CSV export as an agent-callable tool (v1: owner command; cheap to add later — read-only).
- Agent editing of existing word cards.
- Deck sharing between learners (v1: separate vocabularies; sharing via CSV export/import).

### Dependencies / Assumptions

- The Mac mini is available 24/7 with Python 3.12+ (uv), colima (Docker runtime), and internet access; auto-login enabled, system sleep disabled, and colima auto-starts at boot (validated at deployment, not assumed).
- The owner will provision one Telegram bot token and an OpenAI API key. No monthly budget was set: every LLM call is usage-logged per learner, and a local monthly cap is enforced before each call (over cap → skip and notify).
- Initial data: the three CSVs in `data/` load as the owner's three German decks. The git-tracked `progress.sqlite3` contains no progress rows (verified), so there is no SQLite-to-Postgres data migration — initial load is a fresh CSV import; the SQLite file and its code paths are retired.
- Per-learner reminder settings live in the users table and are tuned from real usage; initial values: minimum push interval 3h, quiet hours 22:00–09:00, timezone Europe/Berlin, daily new-word limit 5.

### Sources / Research

- Existing core: `vocab/` (models, storage, SM-2, scheduler, 4 exercise generators), `cli.py` (JSON CLI — the ready tool surface), `tests/test_core.py`.
- Agent tool map and behavior rules: `skills/words-trainer-agent-tools/SKILL.md` (9 tools over the CLI, queue semantics, deterministic-grading rule).
- CSV dictionary formats: `data/*.csv` (nouns with articles, verbs with 18-cell conjugation, verbs with prepositions).
- External (July 2026, verified): aiogram 3.30 / PTB 22.8 docs and changelogs; OpenAI deprecations page (Assistants API sunset 2026-08-26) and pricing page (`gpt-5.4-mini` $0.75/$4.50 per 1M); openai-agents 0.18.3; APScheduler 3.11.3 stable vs 4.0.0a6 "not for production"; Apple launchd docs; docker/for-mac#3567 and #6504 (Docker Desktop/OrbStack cannot run headless — resolved by colima, which runs without a GUI session); Litestream's own cron-backup guidance (moot after the Postgres decision — `pg_dump` replaces it).

---

## Planning Contract

**Product Contract preservation note:** changed twice with owner confirmation — (1) at enrichment: R-IDs tightened and integrity requirements added (flow-analysis findings); (2) post-review scope change: multi-user (spouse in v1), multi-language model, PostgreSQL over SQLite — R6/R9/R10/R11/R12/R15 rewritten, AE10 added, "multi-user" removed from out-of-scope. Doc-review findings folded in: per-user lemma uniqueness (R10), new-queue wording in AE2, deck empty-state (R3/F2), push suppression during long sessions (R12), curator CLI command (U8), import-conflict columns (U1), backup and diagram alignment (HTD/U9), secrets location + .gitignore (U9).

### Key Technical Decisions

- **KTD1. Telegram framework: aiogram 3.x (pin `>=3.30,<4`).** Built-in FSM matches drill states, typed `CallbackData` factory keeps callback payloads under the 64-byte limit, router composition separates drill and chat paths. python-telegram-bot v22 is viable but its ConversationHandler mixing of callbacks and messages is a known footgun. Pre-2024 tutorials for either library are version-incompatible.
- **KTD2. OpenAI integration: openai-agents SDK (`~=0.18`) over the Responses API.** The Assistants API sunsets 2026-08-26 and must not appear anywhere. Word cards are generated via strict structured outputs from one Pydantic `WordCard` model shared with the DB insert/validation path. Model names live in config (default `gpt-5.4-mini` for chat and cards). Disable SDK tracing upload. (Inherits the session-settled "LLM provider — OpenAI".)
- **KTD3. Scheduling: APScheduler 3.11 `AsyncIOScheduler` in-process.** 4.x is still alpha. Start in the bot's `on_startup`; generous `misfire_grace_time` + `coalesce=True`; explicit per-learner timezone; wrap jobs so exceptions are reported to the owner, not swallowed.
- **KTD4. Deployment: bot under launchd; PostgreSQL under colima.** Docker Desktop/OrbStack cannot run headless on macOS, but colima can (Lima VM, `brew services` autostart) — the database runs as a `postgres:16` container with a named volume; the bot process stays a LaunchAgent with `KeepAlive` and absolute `.venv/bin/python` paths (deploy runs `uv sync`; never `uv run` as the service entrypoint). Secrets live in a chmod-600 env file at a fixed path outside the repository (`~/.config/wordsbot/env`); the repo gains a `.gitignore` covering env files, local databases, logs, and backups. In-app rotating log handler. Deployment checklist validates auto-login, `pmset -a sleep 0`, and colima autostart. (Instantiates the session-settled "Mac mini hosting".)
- **KTD5. Data model: PostgreSQL, per-user and per-language from day one.** `users` (chat_id, timezone, reminder settings, LLM cap), `decks` (user_id, language, name), `words` (deck_id, kind, card JSONB, imported_at, modified_at), progress/tasks/reviews/sessions keyed to words (user implied via deck ownership), `pending_cards`, `curator_plans`/`curator_runs` per user, `llm_usage` per user. Lemma uniqueness is per user + language; one word belongs to exactly one deck; collisions reuse the existing word and its progress (R10). Numbered SQL migrations run by a small runner at startup; psycopg 3. The `vocab/` core drops the stdlib-only constraint (psycopg, pydantic allowed); SQLite code paths are removed. (Instantiates the session-settled "PostgreSQL is the single store".)
- **KTD6. DB availability posture.** The bot treats Postgres as a hard dependency: on connection failure it retries with backoff, notifies the owner once, and drops drill interactions with a "storage unavailable" message rather than degrading silently. The Mac mini runs both processes, so co-availability is the norm; the checklist covers container restart.
- **KTD7. Trust boundary invariant.** Only the trainer writes `progress`, `reviews`, and task grading (structurally: grading lives in `scheduler.submit_answer`). Agents get read tools scoped to the acting learner plus exactly two write surfaces: staged card proposals (`pending_cards`, committed by the deterministic confirm handler) and the curator's plan table. No tool ever wraps progress writes; tool output for an unanswered task strips `expected`. (Instantiates the session-settled "grading is deterministic".)
- **KTD8. Curator = deterministic analysis + one structured LLM call, per learner.** Python computes accuracy per word/exercise type, overdue histogram, and error streaks from `reviews` (including raw answers, R20); one structured-output call returns the plan (and digest text on digest runs). Plans land in `curator_plans` (`UNIQUE(user_id, run_date, kind)` — idempotent re-runs); runs logged in `curator_runs` with token usage. The push executor re-validates plan items against the live due queue at send time; deliberate extra-practice drill items are exempt and write SRS progress through the normal path. A plan is fresh until the next scheduled run completes or 24h elapses; stale = absent. No DB-query tools for the curator.
- **KTD9. Message routing order: command → callback → pending-typed-task check (from the DB) → chat agent.** The DB is the source of truth for "is a typed answer pending" per learner; aiogram FSM state is only a routing hint (MemoryStorage is lost on restart).
- **KTD10. Tool-surface parity: vocab function → CLI command → bot/agent tool, in that order.** Every capability lands in the core and CLI first (CLI takes `--user`, defaulting to the owner); `skills/words-trainer-agent-tools/SKILL.md` is updated in the same unit that changes the surface.
- **KTD11. Drill message pattern.** On answer: edit the exercise message in place (verdict shown, keyboard removed), then send the next exercise as a new message. Every callback gets an immediate `answerCallbackQuery`; stale taps get the ephemeral "expired" toast (R18/AE7).
- **KTD12. Language-keyed generator registry.** Exercise generators register per language; German provides all four types (current `vocab/exercises/`); unknown languages fall back to flashcards + choice (R11). Card validators follow the same registry.

### High-Level Technical Design

Component topology — one bot process, one database container, per-learner scheduling:

```mermaid
flowchart TB
    TG[(Telegram API)] <--> DISP[aiogram dispatcher]
    subgraph MAC [Mac mini]
        subgraph PROC [Bot process - launchd KeepAlive]
            DISP --> ROUTE{router}
            ROUTE -->|commands, callbacks, pending typed answer| DRILL[Drill handlers - deterministic]
            ROUTE -->|free text, no pending task| AGENT[Chat agent - openai-agents SDK]
            SCHED[APScheduler 3.11] -->|push job per learner| PUSH[Push composer]
            SCHED -->|daily/weekly per learner| CUR[Curator run]
            DRILL --> CORE[vocab core: grading, SM-2, queues, decks, users]
            PUSH --> CORE
            AGENT -->|read tools + staged writes| CORE
            CUR -->|deterministic stats| CORE
        end
        subgraph COLIMA [colima VM - brew services autostart]
            PG[(PostgreSQL 16 container)]
        end
        BAK[launchd daily job: pg_dump + rotation]
    end
    CORE --> PG
    BAK --> PG
    CUR -->|one structured call| OAI[(OpenAI API)]
    AGENT --> OAI
    CUR --> PLANS[curator_plans per user]
    PLANS --> PUSH
```

Inbound message routing (the rule KTD9 encodes):

```mermaid
flowchart TD
    M[Update from Telegram] --> OWN{chat_id on allowlist?}
    OWN -->|no| DROP[ignore - AE5]
    OWN -->|yes| K{kind?}
    K -->|command| CMD[session control]
    K -->|callback| CB{task open and not voided?}
    CB -->|yes| GRADE[submit_answer -> edit message with verdict]
    CB -->|no| TOAST[ephemeral 'exercise expired' - AE7]
    K -->|free text| T{learner has pending typed-answer task?}
    T -->|yes| GRADE
    T -->|no| LLM[chat agent - F3]
```

### Assumptions

- Config (`bot` layer) carries: bot token, OpenAI key + model names, database URL, monthly LLM cap; per-learner settings (chat_id, timezone, push window/interval, quiet hours, daily new-word limit) live in the `users` table.
- The bot layer takes aiogram, openai-agents, APScheduler, pydantic; the core takes psycopg 3 and pydantic (stdlib-only constraint retired with SQLite).
- Tests run against a disposable Postgres database (the same colima container with a `_test` database, or `docker compose` test service); no test touches live data.

### Risks & Mitigations

- **openai-agents SDK is pre-1.0** — minor versions break. Mitigation: pin `~=0.18`, isolate SDK usage in `bot/agent.py`, keep the fallback of owning the loop on the Responses API.
- **Model lineup churn** (GPT-5.6 landed weeks ago). Mitigation: model names only in config; structured-output schema owned by our Pydantic model, not the prompt.
- **Mac mini sleeps, logs out, or colima fails to start** → pushes stop or the bot loses its DB. Mitigation: deployment checklist validates auto-login + `pmset -a sleep 0` + `brew services` autostart for colima; KTD6 retry-and-notify posture; APScheduler `misfire_grace_time`/`coalesce` absorbs wake-up delays; launchd `KeepAlive` restarts crashes.
- **aiogram FSM state is in-memory** and lost on restart. Mitigation: the DB is the source of truth for pending tasks (KTD9); a restart mid-session degrades to "next exercise", never to wrong grading.
- **Unbounded LLM spend** (no budget was set; now two users). Mitigation: per-learner `llm_usage` logging from day one, local monthly cap checked before every call, curator capped at one structured call per learner per run.
- **Two learners, one bot token** — a routing bug could leak one learner's data to the other. Mitigation: every core query takes an explicit user scope; AE10 is a standing test; tool registry is constructed per acting learner.

---

## Implementation Units

Phase A — core refactor (U1–U5). Phase B — deterministic bot (U6). Phase C — agent (U7–U8). Phase D — ops (U9).

### U1. Postgres foundation: schema, migrations, initial load

- **Goal:** PostgreSQL replaces SQLite as the single store, with users and languages in the schema; owner's German decks loaded.
- **Requirements:** R6, R10, R15, R18–R20 (schema fields); KTD5, KTD6.
- **Dependencies:** none.
- **Files:** `docker-compose.yml`, `vocab/db.py` (rewrite on psycopg 3), `vocab/migrations/` (numbered SQL), `vocab/models.py`, `tests/test_db.py` (new), `tests/test_core.py` (adapt).
- **Approach:** `docker-compose.yml` runs `postgres:16` with a named volume under colima. Migration runner applies numbered SQL files tracked in a `schema_migrations` table. Schema v1: `users`, `decks(user_id, language, name)`, `words(deck_id, kind, card JSONB, imported_at, modified_at)`, `progress`, `tasks(voided_at)`, `reviews(answer)`, `sessions`, `pending_cards`, `curator_plans(user_id)`, `curator_runs`, `llm_usage(user_id)`; unique index enforcing lemma-per-user-per-language. Connection helper with retry/backoff per KTD6. Seed: two users from config; initial load happens via U3's import (no SQLite migration — the tracked DB has zero progress rows; SQLite paths are deleted).
- **Test scenarios:** migrations apply cleanly on an empty database and are idempotent on re-run; lemma uniqueness rejects a duplicate within one user+language and allows the same lemma for the other user; deck FK cascade rules match R22 (deck delete moves words, never drops progress); connection helper surfaces a clear error when Postgres is down.
- **Verification:** full suite green against the test database; `docker compose up -d` + migration runner from scratch produces the working schema.

### U2. Word lifecycle: creation, validation registry, staging, deck ops

- **Goal:** the first non-CSV write path for words, with per-language validation agents depend on.
- **Requirements:** R6, R9, R10, R21, R22; KTD5, KTD7, KTD12.
- **Dependencies:** U1.
- **Files:** `vocab/words.py` (new), `vocab/languages.py` (new registry), `vocab/exercises/grammar.py`, `vocab/exercises/cloze.py`, `tests/test_words.py` (new).
- **Approach:** `validate_card(language, kind, fields)` dispatches through the language registry (German: verb = 3 tenses × 6 persons, noun = article + plural, verb_prep = preposition + case; warn when the example sentence contains no recognizable form of the word). `stage_cards` / `commit_pending` / `reject_pending` implement staging; commit applies the R10 collision rule. Deck ops: `create_deck`, `rename_deck`, `delete_deck` (words → learner's general deck), `move_word`. Generator registry maps language → available exercise types with the R11 fallback. Fix the core crash: `grammar._verb_task` returns None on empty/partial conjugation instead of raising.
- **Test scenarios:** partial verb card rejected (AE9); valid cards commit and appear in the learner's new queue; duplicate lemma within user+language reuses the word and keeps progress, same lemma across users creates independent words (AE10 corollary); deck delete moves words with progress intact; unknown-language deck yields only flashcard/choice tasks (R11); grammar generator on a partial-conjugation word falls back instead of crashing; staged cards survive a process restart.
- **Verification:** `tests/test_words.py` green; a committed card immediately serves a stage-0 task via the scheduler.

### U3. CSV import/export

- **Goal:** deck-scoped import/export matching the ownership model (DB wins), doubling as the initial data load.
- **Requirements:** R7, R8; KTD5.
- **Dependencies:** U1, U2 (validation reused).
- **Files:** `vocab/storage.py`, `cli.py`, `tests/test_import_export.py` (new).
- **Approach:** `import_csv(path, user, deck_name, language)` — parse rows with the existing per-row detection, insert new lemmas into the deck, apply the R10 collision rule across the user's decks, skip-and-report rows whose DB card was modified after its last import (`imported_at`/`modified_at`); never touch progress. `export_deck(user, deck)` emits the same kind-specific row formats the parser accepts. Initial load: the three `data/*.csv` files into the owner's three German decks.
- **Test scenarios:** import creates deck + words; re-import → zero adds, zero updates, progress intact (AE4); re-import after a DB-side card edit reports the conflict and keeps the DB card; export → import round-trip on a deck with all four word kinds reproduces identical cards; the three real CSVs load with expected counts.
- **Verification:** round-trip test green against the three real CSVs in `data/`.

### U4. Session and task integrity in the core scheduler

- **Goal:** the per-learner invariants that make Telegram interaction safe for SRS data.
- **Requirements:** R1, R3, R12, R18, R19, R23, R24; KTD8 (plan consumption).
- **Dependencies:** U1.
- **Files:** `vocab/scheduler.py`, `vocab/db.py`, `tests/test_sessions.py` (new).
- **Approach:** all queue queries take a user scope. `create_task` voids the learner's previous open task; `submit_answer` refuses voided/expired tasks with a distinct error; TTL sweep voids tasks older than 24h. `sessions` rows track kind, deck scope, counters; auto-close after 30 idle minutes. Deck-scoped due/new queue variants; micro-session composition = due words only, cap 5; `compose_push(user, plan)` consumes the learner's newest fresh plan (KTD8 freshness), re-validates items, falls back to deterministic order. Raw answer stored on `reviews`. Push eligibility helper enforces min interval + quiet hours + no-open-long-session (R12).
- **Test scenarios:** issuing task B voids task A, answering A returns the expired error and writes nothing (AE7); free-text binding is unambiguous with at most one open task per learner (AE8); two learners' open tasks do not interfere (AE10); deck-scoped session drains deck due → deck new → ends, empty start returns the R3 empty signal; stale plan items already reviewed are dropped; no fresh plan → deterministic fallback (AE6); lapse-retried word does not satisfy push eligibility alone (R23); open long session suppresses push eligibility (R12).
- **Verification:** `tests/test_sessions.py` green; existing scheduler behavior tests still pass under the user scope.

### U5. CLI and agent tool surface update

- **Goal:** every core capability reachable via CLI with user scoping; the documented tool map matches reality.
- **Requirements:** R4 (task context read), R13 (history read); KTD10.
- **Dependencies:** U2, U3, U4.
- **Files:** `cli.py`, `skills/words-trainer-agent-tools/SKILL.md`, `README.md`, smoke additions in `tests/`.
- **Approach:** global `--user` flag (default: owner). New commands: `deck list/create/rename/delete/move`, `import <csv> --deck --language`, `export <deck>`, `task-context <task_id>` (strips `expected` for unanswered tasks), `history [--word]`, `push-plan get/set`, `propose-words` / `confirm-pending`. SKILL.md gains the new tools, user-scoping semantics, and keeps the deterministic-grading rules.
- **Test scenarios:** each command emits valid JSON against the test database; `task-context` on an unanswered task contains no `expected`; `--user` isolation: user A's commands never return user B's data; parity: every bot capability in U6–U8 has a CLI equivalent.
- **Verification:** smoke script exercises every command; SKILL.md tool table matches `cli.py --help`.

### U6. Telegram bot: deterministic drill loop and pushes

- **Goal:** a fully usable product for both learners — pushes, micro-sessions, long sessions — with zero LLM code.
- **Requirements:** R1, R2, R3, R12, R15, R17, R23, R24; KTD1, KTD3, KTD9, KTD11.
- **Dependencies:** U4, U5.
- **Files:** `bot/__init__.py`, `bot/main.py`, `bot/config.py`, `bot/middleware.py` (allowlist + user resolution), `bot/drill.py`, `bot/keyboards.py`, `bot/push.py`, `tests/test_bot_routing.py` (new; pure-helper tests).
- **Approach:** aiogram Dispatcher with routers; allowlist middleware resolves chat_id → user and drops everything else (AE5). Inline keyboards only; `CallbackData` packs `task_id` + option index. Routing per KTD9 with the pending-task check scoped to the resolved learner. Verdict = edit-in-place + keyboard removal, next exercise = new message; every callback answered immediately; expired taps → toast. APScheduler jobs iterate learners: push (eligibility helper from U4, per-learner timezone/quiet hours), TTL sweep, session auto-close.
- **Execution note:** mostly integration wiring; prefer runtime smoke verification (test bot token + test database) over exhaustive unit coverage; unit-test only the pure helpers (routing decision, keyboard packing, push eligibility).
- **Test scenarios:** routing helper quadrants (AE8); callback on voided task → expired path (AE7); non-allowlisted chat_id dropped (AE5); push eligibility respects interval, quiet hours, and open long sessions per learner; callback data stays ≤64 bytes.
- **Verification:** manual smoke on a test bot with both test users: receive a push, complete a micro-session, run a deck-scoped long session, tap an old button, verify the other user sees nothing (AE10).

### U7. Chat agent: explanations and word creation

- **Goal:** F3 — the LLM joins as a per-learner tool-using tutor with staged writes.
- **Requirements:** R4, R5, R6, R9, R16, R21; KTD2, KTD7, KTD10.
- **Dependencies:** U2, U5, U6.
- **Files:** `bot/agent.py`, `bot/tools.py`, `bot/chat.py` (handlers + staging preview/confirm), `tests/test_agent_tools.py` (new).
- **Approach:** openai-agents SDK Agent whose tool registry is constructed for the acting learner: read tools (word card, stats, due, history, task context, current push plan + last curator log) and one staged write (`propose_words`, deck proposal included, created on confirm). `WordCard` Pydantic model is both the structured `output_type` and the input to `vocab.words.validate_card`. Per-learner session history with idle expiry and `max_turns`; "explain" button carries `task_id` with full post-verdict context. Every call logged to `llm_usage` with the learner; per-learner monthly-cap check before each call (over cap → polite refusal, drills unaffected).
- **Test scenarios (no network — tools tested directly, LLM mocked):** tools never expose `expected` pre-grading; tools never return another learner's data (AE10); `propose_words` with an invalid card surfaces the validation error (AE9); confirm commits, cancel discards, restart mid-staging keeps pending rows; no tool in the registry can write progress/reviews; cap-exceeded path skips the API call.
- **Verification:** live smoke: "explain" after a wrong answer; teach a topic → preview → confirm → word appears in the learner's new queue (AE2 end-to-end); `llm_usage` rows carry the right user.

### U8. Curator: analysis, plan, digest — per learner

- **Goal:** F4 — scheduled per-learner analysis composing pushes and digests, with the fallback already in place.
- **Requirements:** R13, R14, R20, R24; KTD8, KTD10.
- **Dependencies:** U4, U7 (shares OpenAI client, usage logging).
- **Files:** `vocab/analysis.py` (new; deterministic stats), `bot/curator.py`, `cli.py` (`curator-run --user --kind {plan,digest}`), `tests/test_curator.py` (new).
- **Approach:** `analysis.py` computes per-word/per-type accuracy, overdue histogram, error streaks with raw answers — pure Python over one learner's `reviews`. `bot/curator.py` feeds one compact JSON blob per learner to one structured-output call → plan JSON (+ digest text on the weekly kind); upsert into `curator_plans` on `(user_id, run_date, kind)`; `curator_runs` row with status and token usage; failures leave the previous plan untouched and notify the owner. The `curator-run` CLI command (KTD10) triggers a run manually for testing. Digest is one Telegram message per learner per period regardless of retries.
- **Test scenarios (LLM mocked):** analysis on a seeded DB is deterministic and single-learner-scoped; re-run for the same period upserts (one plan row, one digest); mid-run failure leaves the old plan intact; plan items already reviewed are filtered by U4 re-validation; curator never writes outside its plan/run tables.
- **Verification:** `cli.py curator-run --user owner --kind plan` against the test DB produces an inspectable plan row; next push composes from it; with OpenAI access removed the next push still fires (AE6).

### U9. Deployment and operations on the Mac mini

- **Goal:** bot + database survive reboots, crashes, and disk failures without attention.
- **Requirements:** Dependencies/Assumptions section; KTD4, KTD6.
- **Dependencies:** U6 (deployable product; U7/U8 hot-deploy later the same way).
- **Files:** `deploy/com.dykhalkin.wordsbot.plist`, `deploy/backup.plist`, `scripts/deploy.sh`, `scripts/backup.sh`, `.gitignore`, `README.md` (ops section), `bot/logging.py` (rotating handler).
- **Approach:** LaunchAgent with `KeepAlive` and absolute `.venv/bin/python -m bot` entrypoint; `deploy.sh` = `uv sync` + `docker compose up -d` + migrations + plist install. colima autostart via `brew services start colima`. Secrets in `~/.config/wordsbot/env` (chmod 600), outside the working tree; `.gitignore` covers env files, `*.sqlite3`, logs, backups (and removes `progress.sqlite3` + `__pycache__` from tracking). Daily backup: launchd job running `pg_dump` with 14-day rotation into an iCloud-synced folder. In-app `RotatingFileHandler`. README checklist: auto-login, `pmset -a sleep 0`, colima autostart, restore-from-backup drill, token/key rotation steps.
- **Execution note:** packaging/config unit — verify by install/runtime smoke, not unit tests (`Test expectation: none — configuration artifacts; verified by the deployment checklist`).
- **Verification:** reboot the mini → colima, Postgres, and the bot all come back without manual action; `launchctl kickstart -k` and `kill -9` recover; `pg_dump` backup restores into a working database.

---

## Verification Contract

| Gate | Command / check | Applies to |
|---|---|---|
| Unit tests | `python3 -m unittest discover -s tests` against the test database | every unit; green after each unit lands |
| Schema from scratch | `docker compose up -d` + migration runner on an empty volume | U1, and re-run before first deploy |
| CLI parity smoke | every `cli.py` command (incl. `--user`) returns valid JSON against the test DB | U5, U8, and any later tool change |
| Bot smoke | test-token run with two test users: push → micro-session → long session → stale tap → foreign chat_id → cross-user isolation | U6, U7, U8 |
| LLM-free guarantee | with no `OPENAI_API_KEY`, pushes and drills fully work (AE6) | U6, U8 |
| Ops drill | reboot survival (colima + Postgres + bot) + `pg_dump` restore | U9 |

No LLM call in any automated test: agent tools and curator analysis are tested directly; model calls are mocked.

## Definition of Done

- All nine units landed in dependency order; the full test suite passes; AE1–AE10 each covered by an automated test or a named smoke step.
- The bot runs on the Mac mini (launchd + colima Postgres), survives a reboot, and has produced for the owner at least one real push micro-session, one long session, one agent word-creation, and one curator plan + digest; the spouse's account is onboarded with its own deck and receives its own pushes.
- The three CSVs are loaded as the owner's German decks with verified counts; SQLite code paths and the tracked `progress.sqlite3` are removed from the repo.
- `skills/words-trainer-agent-tools/SKILL.md`, `README.md`, and CLI `--help` agree on the tool surface.
- No abandoned experimental code: dead ends removed from the diff before completion.
