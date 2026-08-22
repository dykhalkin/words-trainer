ALTER TABLE words
    DROP CONSTRAINT words_kind_check,
    ADD CONSTRAINT words_kind_check CHECK (
        kind IN ('noun', 'verb', 'verb_prep', 'phrase', 'other')
    );
