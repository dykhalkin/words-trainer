ALTER TABLE tasks
    ADD CONSTRAINT uq_tasks_id_user UNIQUE (id, user_id),
    ADD COLUMN response_mode TEXT,
    ADD COLUMN answer_language TEXT,
    ADD COLUMN grading_policy TEXT;

UPDATE tasks t
SET response_mode = CASE
        WHEN jsonb_typeof(t.payload->'options') = 'array'
             AND jsonb_array_length(t.payload->'options') > 0 THEN 'choice'
        ELSE 'free_text'
    END,
    answer_language = CASE
        WHEN jsonb_typeof(t.payload->'options') = 'array'
             AND jsonb_array_length(t.payload->'options') > 0 THEN NULL
        ELSE w.language
    END,
    grading_policy = CASE
        WHEN jsonb_typeof(t.payload->'options') = 'array'
             AND jsonb_array_length(t.payload->'options') > 0 THEN 'deterministic'
        ELSE 'tutor_on_mismatch'
    END
FROM words w
WHERE w.id = t.word_id;

UPDATE tasks
SET status = 'voided', voided_at = now()
WHERE status = 'open' AND type = 'flashcard_de_ru';

ALTER TABLE tasks
    ALTER COLUMN response_mode SET NOT NULL,
    ALTER COLUMN grading_policy SET NOT NULL,
    ADD CONSTRAINT ck_tasks_response_mode
        CHECK (response_mode IN ('choice', 'free_text')),
    ADD CONSTRAINT ck_tasks_grading_policy
        CHECK (grading_policy IN ('deterministic', 'tutor_on_mismatch')),
    ADD CONSTRAINT ck_tasks_answer_language
        CHECK ((response_mode = 'choice' AND answer_language IS NULL)
            OR (response_mode = 'free_text' AND answer_language IS NOT NULL));

CREATE TABLE answer_evaluations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    answer TEXT NOT NULL,
    answer_hash TEXT NOT NULL,
    deterministic_result JSONB NOT NULL,
    context JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'succeeded', 'failed', 'discarded')),
    decision TEXT CHECK (decision IN ('accepted', 'partial', 'rejected')),
    quality INTEGER CHECK (quality BETWEEN 0 AND 5),
    feedback TEXT,
    model TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    FOREIGN KEY (task_id, user_id)
        REFERENCES tasks(id, user_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_answer_evaluations_pending
    ON answer_evaluations(task_id) WHERE status = 'pending';
CREATE INDEX idx_answer_evaluations_task
    ON answer_evaluations(task_id, created_at DESC);

ALTER TABLE reviews
    ADD COLUMN grading_source TEXT NOT NULL DEFAULT 'deterministic'
        CHECK (grading_source IN ('deterministic', 'tutor', 'learner_override')),
    ADD COLUMN answer_evaluation_id TEXT REFERENCES answer_evaluations(id) ON DELETE SET NULL,
    ADD COLUMN grader_feedback TEXT;

CREATE TABLE reminder_policies (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'off' CHECK (mode IN ('smart', 'off')),
    days_mask SMALLINT NOT NULL DEFAULT 127 CHECK (days_mask BETWEEN 1 AND 127),
    window_start TIME NOT NULL DEFAULT '09:00',
    window_end TIME NOT NULL DEFAULT '21:00',
    target_interval_minutes INTEGER NOT NULL DEFAULT 180
        CHECK (target_interval_minutes BETWEEN 60 AND 1440),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    planning_generation INTEGER NOT NULL DEFAULT 1 CHECK (planning_generation > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (window_start <> window_end)
);

INSERT INTO reminder_policies(
    user_id, mode, days_mask, window_start, window_end,
    target_interval_minutes, revision, planning_generation
)
SELECT id,
       CASE WHEN quiet_start = quiet_end THEN 'off' ELSE 'smart' END,
       127,
       CASE WHEN quiet_start = quiet_end THEN TIME '09:00' ELSE quiet_end END,
       CASE WHEN quiet_start = quiet_end THEN TIME '21:00' ELSE quiet_start END,
       greatest(60, least(1440, min_push_interval_minutes)),
       1,
       1
FROM users
ON CONFLICT (user_id) DO NOTHING;

CREATE TABLE reminder_plans (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    policy_revision INTEGER NOT NULL,
    planning_generation INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('curator', 'deterministic')),
    horizon_start TIMESTAMPTZ NOT NULL,
    horizon_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted', 'superseded', 'failed')),
    suppression_reason TEXT CHECK (
        suppression_reason IS NULL OR suppression_reason IN (
            'no_due', 'recent_practice', 'active_session',
            'low_due_load', 'recently_ignored'
        )
    ),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, policy_revision, planning_generation)
);

ALTER TABLE deliveries
    DROP CONSTRAINT deliveries_status_check,
    ALTER COLUMN claimed_at DROP NOT NULL,
    ALTER COLUMN claimed_at DROP DEFAULT,
    ADD COLUMN scheduled_for TIMESTAMPTZ,
    ADD COLUMN reminder_plan_id BIGINT REFERENCES reminder_plans(id) ON DELETE SET NULL,
    ADD COLUMN reminder_revision INTEGER,
    ADD COLUMN source TEXT CHECK (source IS NULL OR source IN ('legacy', 'curator', 'deterministic')),
    ADD COLUMN skip_reason TEXT,
    ADD CONSTRAINT deliveries_status_check CHECK (
        status IN ('scheduled', 'claimed', 'sent', 'failed', 'released', 'skipped', 'cancelled')
    );

UPDATE deliveries SET source = 'legacy' WHERE kind = 'push' AND source IS NULL;

CREATE INDEX idx_deliveries_due
    ON deliveries(status, scheduled_for) WHERE status = 'scheduled';

ALTER TABLE curator_plans
    DROP CONSTRAINT curator_plans_user_id_run_date_kind_key,
    ADD COLUMN input_revision INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN planning_generation INTEGER NOT NULL DEFAULT 1,
    ADD CONSTRAINT uq_curator_plans_revision
        UNIQUE (user_id, run_date, kind, input_revision, planning_generation);

ALTER TABLE curator_runs
    ADD COLUMN input_revision INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN planning_generation INTEGER NOT NULL DEFAULT 1;

CREATE UNIQUE INDEX uq_curator_runs_active_generation
    ON curator_runs(user_id, kind, input_revision, planning_generation)
    WHERE status = 'running';

CREATE TABLE telegram_pending_actions (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN (
        'reminder_window_start', 'reminder_window_end', 'reminder_cadence',
        'reminder_days', 'reminder_confirm'
    )),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reminder_refresh_requests (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    requested_revision INTEGER NOT NULL,
    requested_generation INTEGER NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO reminder_refresh_requests(user_id, requested_revision, requested_generation)
SELECT user_id, revision, planning_generation
FROM reminder_policies
WHERE mode = 'smart'
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO job_controls(job_name) VALUES ('reminder_refresh')
ON CONFLICT(job_name) DO NOTHING;
