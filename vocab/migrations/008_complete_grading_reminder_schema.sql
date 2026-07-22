-- Forward-only repair for installations that applied migration 006 before its
-- final reminder-refresh and deterministic-grading additions were present.

ALTER TABLE answer_evaluations
    ADD COLUMN IF NOT EXISTS deterministic_result JSONB;

UPDATE answer_evaluations
SET deterministic_result = '{}'::jsonb
WHERE deterministic_result IS NULL;

ALTER TABLE answer_evaluations
    ALTER COLUMN deterministic_result SET NOT NULL;

ALTER TABLE telegram_pending_actions
    DROP CONSTRAINT IF EXISTS telegram_pending_actions_kind_check;

ALTER TABLE telegram_pending_actions
    ADD CONSTRAINT telegram_pending_actions_kind_check CHECK (kind IN (
        'reminder_window_start', 'reminder_window_end', 'reminder_cadence',
        'reminder_days', 'reminder_confirm'
    ));

CREATE TABLE IF NOT EXISTS reminder_refresh_requests (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    requested_revision INTEGER NOT NULL,
    requested_generation INTEGER NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO reminder_refresh_requests(user_id, requested_revision, requested_generation)
SELECT user_id, revision, planning_generation
FROM reminder_policies
WHERE mode = 'smart'
ON CONFLICT (user_id) DO UPDATE SET
    requested_revision = EXCLUDED.requested_revision,
    requested_generation = EXCLUDED.requested_generation,
    requested_at = now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_curator_runs_active_generation
    ON curator_runs(user_id, kind, input_revision, planning_generation)
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_deliveries_due
    ON deliveries(status, scheduled_for) WHERE status = 'scheduled';

INSERT INTO job_controls(job_name) VALUES ('reminder_refresh')
ON CONFLICT(job_name) DO NOTHING;
