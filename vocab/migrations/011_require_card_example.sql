DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM words
        WHERE jsonb_typeof(card) <> 'object'
           OR jsonb_typeof(card->'example') <> 'string'
           OR btrim(COALESCE(card->>'example', '')) = ''
    ) THEN
        RAISE EXCEPTION 'cannot require card examples: words with missing or empty example exist';
    END IF;
END $$;

ALTER TABLE words
    ADD CONSTRAINT ck_words_card_example CHECK (
        jsonb_typeof(card) = 'object'
        AND jsonb_typeof(card->'example') = 'string'
        AND btrim(COALESCE(card->>'example', '')) <> ''
    );
