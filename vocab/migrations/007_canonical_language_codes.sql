-- Persist languages as lowercase ISO 639-1 codes. Historical imports accepted
-- display names, so merge the known German alias into the canonical `de` scope.

ALTER TABLE words DROP CONSTRAINT words_deck_id_user_id_language_fkey;

DELETE FROM decks alias_deck
USING decks canonical_deck
WHERE alias_deck.user_id = canonical_deck.user_id
  AND lower(alias_deck.language) IN ('german', 'deutsch')
  AND canonical_deck.language = 'de'
  AND alias_deck.normalized_name = canonical_deck.normalized_name
  AND NOT EXISTS (SELECT 1 FROM words WHERE deck_id = alias_deck.id);

UPDATE decks
SET language = 'de', updated_at = now()
WHERE lower(language) IN ('german', 'deutsch');

UPDATE words
SET language = 'de', modified_at = now()
WHERE lower(language) IN ('german', 'deutsch');

UPDATE pending_cards
SET language = 'de'
WHERE lower(language) IN ('german', 'deutsch');

UPDATE tasks
SET answer_language = 'de'
WHERE lower(answer_language) IN ('german', 'deutsch');

ALTER TABLE words
    ADD CONSTRAINT words_deck_id_user_id_language_fkey
    FOREIGN KEY (deck_id, user_id, language)
    REFERENCES decks(id, user_id, language) ON DELETE RESTRICT;

ALTER TABLE decks
    ADD CONSTRAINT ck_decks_language_code CHECK (language ~ '^[a-z]{2}$');
ALTER TABLE words
    ADD CONSTRAINT ck_words_language_code CHECK (language ~ '^[a-z]{2}$');
ALTER TABLE pending_cards
    ADD CONSTRAINT ck_pending_cards_language_code CHECK (language ~ '^[a-z]{2}$');
ALTER TABLE tasks
    ADD CONSTRAINT ck_tasks_answer_language_code
    CHECK (answer_language IS NULL OR answer_language ~ '^[a-z]{2}$');
