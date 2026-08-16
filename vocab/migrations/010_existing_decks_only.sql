ALTER TABLE pending_cards
    ADD COLUMN deck_id BIGINT;

UPDATE pending_cards p
SET deck_id = d.id
FROM decks d
WHERE d.user_id = p.user_id
  AND d.language = p.language
  AND d.normalized_name = lower(regexp_replace(btrim(p.deck_name), '\s+', ' ', 'g'));

-- Consolidate the accidental Tutor-created decks for the existing owner.
UPDATE words w
SET deck_id = target.id, modified_at = now()
FROM decks source
JOIN users u ON u.id = source.user_id
JOIN decks target
  ON target.user_id = source.user_id
 AND target.language = source.language
 AND target.normalized_name = 'b1.1'
WHERE w.deck_id = source.id
  AND u.name = 'owner'
  AND source.language = 'de'
  AND source.normalized_name IN (
      'german', 'german vocabulary', 'общий', 'основной', 'общий немецкий'
  );

UPDATE sessions s
SET deck_id = target.id
FROM decks source
JOIN users u ON u.id = source.user_id
JOIN decks target
  ON target.user_id = source.user_id
 AND target.language = source.language
 AND target.normalized_name = 'b1.1'
WHERE s.deck_id = source.id
  AND u.name = 'owner'
  AND source.language = 'de'
  AND source.normalized_name IN (
      'german', 'german vocabulary', 'общий', 'основной', 'общий немецкий'
  );

UPDATE reviews r
SET deck_id = target.id
FROM decks source
JOIN users u ON u.id = source.user_id
JOIN decks target
  ON target.user_id = source.user_id
 AND target.language = source.language
 AND target.normalized_name = 'b1.1'
WHERE r.deck_id = source.id
  AND u.name = 'owner'
  AND source.language = 'de'
  AND source.normalized_name IN (
      'german', 'german vocabulary', 'общий', 'основной', 'общий немецкий'
  );

UPDATE pending_cards p
SET deck_id = target.id
FROM decks source
JOIN users u ON u.id = source.user_id
JOIN decks target
  ON target.user_id = source.user_id
 AND target.language = source.language
 AND target.normalized_name = 'b1.1'
WHERE p.deck_id = source.id
  AND u.name = 'owner'
  AND source.language = 'de'
  AND source.normalized_name IN (
      'german', 'german vocabulary', 'общий', 'основной', 'общий немецкий'
  );

DELETE FROM decks d
USING users u
WHERE d.user_id = u.id
  AND u.name = 'owner'
  AND d.language = 'de'
  AND d.normalized_name IN (
      'german', 'german vocabulary', 'общий', 'основной', 'общий немецкий'
  )
  AND NOT EXISTS (SELECT 1 FROM words w WHERE w.deck_id = d.id)
  AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.deck_id = d.id)
  AND NOT EXISTS (SELECT 1 FROM pending_cards p WHERE p.deck_id = d.id);

-- Empty system General decks disappear. A non-empty legacy General survives as
-- an ordinary deck so a migration can never discard learner data.
DELETE FROM decks d
WHERE d.is_general
  AND NOT EXISTS (SELECT 1 FROM words w WHERE w.deck_id = d.id)
  AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.deck_id = d.id)
  AND NOT EXISTS (SELECT 1 FROM pending_cards p WHERE p.deck_id = d.id);

UPDATE decks SET is_general = false WHERE is_general;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pending_cards WHERE deck_id IS NULL) THEN
        RAISE EXCEPTION 'cannot map every pending card proposal to an existing deck';
    END IF;
END $$;

ALTER TABLE pending_cards
    ALTER COLUMN deck_id SET NOT NULL,
    ADD CONSTRAINT fk_pending_cards_deck_owner_language
        FOREIGN KEY (deck_id, user_id, language)
        REFERENCES decks(id, user_id, language) ON DELETE RESTRICT,
    DROP COLUMN deck_name;

ALTER TABLE decks DROP CONSTRAINT ck_decks_special_kind;
DROP INDEX uq_decks_general;
ALTER TABLE decks DROP COLUMN is_general;
