CREATE TABLE job_controls (
    job_name TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job_runs (
    id BIGSERIAL PRIMARY KEY,
    job_name TEXT NOT NULL REFERENCES job_controls(job_name) ON DELETE RESTRICT,
    trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'manual')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    force_run BOOLEAN NOT NULL DEFAULT false,
    idempotency_key TEXT UNIQUE,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    result JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_job_runs_recent
    ON job_runs(job_name, requested_at DESC);

CREATE INDEX idx_job_runs_queued
    ON job_runs(requested_at, id) WHERE status = 'queued';

INSERT INTO job_controls(job_name)
VALUES
    ('push'),
    ('curator_plan'),
    ('weekly_digest'),
    ('task_sweep'),
    ('session_cleanup')
ON CONFLICT(job_name) DO NOTHING;
