ALTER TABLE telegram_pending_actions
    DROP CONSTRAINT IF EXISTS telegram_pending_actions_kind_check;

ALTER TABLE telegram_pending_actions
    ADD CONSTRAINT telegram_pending_actions_kind_check CHECK (kind IN (
        'reminder_window_start', 'reminder_window_end', 'reminder_cadence',
        'reminder_days', 'reminder_confirm', 'card_feedback'
    ));

CREATE TABLE card_corrections (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    word_id BIGINT NOT NULL,
    task_id TEXT NOT NULL,
    feedback TEXT NOT NULL,
    original_card JSONB NOT NULL,
    corrected_card JSONB NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (word_id, user_id)
        REFERENCES words(id, user_id) ON DELETE CASCADE,
    FOREIGN KEY (task_id, user_id)
        REFERENCES tasks(id, user_id) ON DELETE CASCADE
);

CREATE INDEX idx_card_corrections_word_created
    ON card_corrections(word_id, created_at DESC);
