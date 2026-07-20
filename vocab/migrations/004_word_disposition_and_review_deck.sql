ALTER TABLE decks
    ADD COLUMN is_archive BOOLEAN NOT NULL DEFAULT false,
    ADD CONSTRAINT ck_decks_special_kind CHECK (NOT (is_general AND is_archive));

UPDATE decks
SET is_archive = true, updated_at = now()
WHERE normalized_name = 'archive' AND NOT is_general;

CREATE UNIQUE INDEX uq_decks_archive
    ON decks(user_id, language) WHERE is_archive;

ALTER TABLE words
    ADD COLUMN card_status TEXT NOT NULL DEFAULT 'active'
        CHECK (card_status IN ('active', 'needs_fix')),
    ADD COLUMN needs_fix_at TIMESTAMPTZ,
    ADD COLUMN needs_fix_reason TEXT,
    ADD COLUMN needs_fix_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;

CREATE INDEX idx_words_study_eligibility
    ON words(user_id, deck_id, card_status);

ALTER TABLE reviews
    ADD COLUMN deck_id BIGINT REFERENCES decks(id) ON DELETE SET NULL;

UPDATE reviews r
SET deck_id = w.deck_id
FROM words w
WHERE w.id = r.word_id;

CREATE INDEX idx_reviews_user_deck_created
    ON reviews(user_id, deck_id, created_at DESC);

