ALTER TABLE tasks
    ADD COLUMN objective TEXT NOT NULL DEFAULT 'recall',
    ADD COLUMN visible_constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN material_version TEXT,
    ADD COLUMN assistance_level INTEGER NOT NULL DEFAULT 0 CHECK (assistance_level >= 0),
    ADD COLUMN answer_disclosed BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE tasks
    DROP CONSTRAINT ck_tasks_answer_language,
    ADD CONSTRAINT ck_tasks_answer_language CHECK (
        response_mode = 'choice'
        OR (response_mode = 'free_text' AND answer_language IS NOT NULL)
    );

ALTER TABLE tasks
    ADD CONSTRAINT ck_tasks_objective CHECK (
        objective IN (
            'introduction', 'recognition', 'recall', 'target_usage',
            'free_expression', 'contrast'
        )
    );

CREATE TABLE task_assistance_events (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('hint', 'disclosure')),
    level INTEGER NOT NULL DEFAULT 0 CHECK (level >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (task_id, user_id) REFERENCES tasks(id, user_id) ON DELETE CASCADE
);

CREATE INDEX idx_task_assistance_events_task
    ON task_assistance_events(task_id, created_at);

ALTER TABLE answer_evaluations
    ADD COLUMN conditions_met BOOLEAN,
    ADD COLUMN target_reproduced BOOLEAN,
    ADD COLUMN defect_type TEXT,
    ADD COLUMN srs_action TEXT CHECK (srs_action IS NULL OR srs_action IN ('review', 'neutral'));

ALTER TABLE sessions
    ADD COLUMN assessed_count INTEGER NOT NULL DEFAULT 0 CHECK (assessed_count >= 0);
