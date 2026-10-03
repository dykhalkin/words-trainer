-- 015 has already been applied locally. Keep it immutable and adapt forward.
ALTER TABLE sessions ADD COLUMN comparison JSONB;
ALTER TABLE sessions ADD CONSTRAINT ck_sessions_comparison CHECK (
    comparison IS NULL OR (kind = 'context' AND jsonb_typeof(comparison) = 'object')
);

-- Previous versions counted every answered task unless explicitly unassessed.
UPDATE sessions s SET assessed_count = s.answered_count - (
    SELECT count(*) FROM tasks t
    WHERE t.session_id = s.id AND t.status = 'answered'
      AND t.verdict->>'assessed' = 'false'
);

-- Old messages may contain hidden requirements or obsolete exercise types.
-- Preserve answers and reviews; only supersede unfinished tasks/evaluations.
UPDATE tasks SET status = 'voided', voided_at = now() WHERE status = 'open';
UPDATE answer_evaluations SET status = 'discarded', finished_at = now(),
    error = 'exercise contract updated'
WHERE status = 'pending';

CREATE INDEX idx_tasks_session_word ON tasks(session_id, word_id);
