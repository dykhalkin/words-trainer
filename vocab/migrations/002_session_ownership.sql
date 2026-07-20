ALTER TABLE decks
    ADD CONSTRAINT uq_decks_id_user UNIQUE (id, user_id);

ALTER TABLE sessions
    ADD CONSTRAINT uq_sessions_id_user UNIQUE (id, user_id),
    DROP CONSTRAINT sessions_deck_id_fkey,
    ADD CONSTRAINT fk_sessions_deck_owner
        FOREIGN KEY (deck_id, user_id) REFERENCES decks(id, user_id) ON DELETE RESTRICT;

ALTER TABLE tasks
    DROP CONSTRAINT tasks_session_id_fkey,
    ADD CONSTRAINT fk_tasks_session_owner
        FOREIGN KEY (session_id, user_id) REFERENCES sessions(id, user_id) ON DELETE SET NULL (session_id);
