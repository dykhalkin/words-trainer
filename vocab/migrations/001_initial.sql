CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    chat_id BIGINT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'Europe/Berlin',
    min_push_interval_minutes INTEGER NOT NULL DEFAULT 180 CHECK (min_push_interval_minutes > 0),
    quiet_start TIME NOT NULL DEFAULT '22:00',
    quiet_end TIME NOT NULL DEFAULT '09:00',
    daily_new_limit INTEGER NOT NULL DEFAULT 5 CHECK (daily_new_limit >= 0),
    llm_monthly_cap_usd NUMERIC(10, 2) NOT NULL DEFAULT 20.00 CHECK (llm_monthly_cap_usd >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE decks (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    is_general BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, user_id, language),
    UNIQUE (user_id, language, normalized_name)
);

CREATE UNIQUE INDEX uq_decks_general
    ON decks(user_id, language) WHERE is_general;

CREATE TABLE words (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    deck_id BIGINT NOT NULL,
    lemma TEXT NOT NULL,
    lemma_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('noun', 'verb', 'verb_prep', 'other')),
    card JSONB NOT NULL,
    modified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, user_id),
    UNIQUE (user_id, language, lemma_key),
    FOREIGN KEY (deck_id, user_id, language)
        REFERENCES decks(id, user_id, language) ON DELETE RESTRICT
);

CREATE INDEX idx_words_deck ON words(deck_id);

CREATE TABLE word_import_state (
    word_id BIGINT PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    imported_card_hash TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE progress (
    word_id BIGINT PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
    reps INTEGER NOT NULL DEFAULT 0 CHECK (reps >= 0),
    lapses INTEGER NOT NULL DEFAULT 0 CHECK (lapses >= 0),
    ease DOUBLE PRECISION NOT NULL DEFAULT 2.5 CHECK (ease >= 1.3),
    interval_days DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (interval_days >= 0),
    due_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_progress_due ON progress(due_at);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('micro', 'long')),
    deck_id BIGINT REFERENCES decks(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'completed', 'stopped', 'expired')),
    target_count INTEGER,
    answered_count INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX uq_sessions_open ON sessions(user_id) WHERE status = 'open';

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_id BIGINT NOT NULL,
    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    payload JSONB NOT NULL,
    expected JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'voided')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours'),
    answered_at TIMESTAMPTZ,
    voided_at TIMESTAMPTZ,
    answer TEXT,
    correct BOOLEAN,
    verdict JSONB,
    FOREIGN KEY (word_id, user_id) REFERENCES words(id, user_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_tasks_open ON tasks(user_id) WHERE status = 'open';
CREATE INDEX idx_tasks_expiry ON tasks(expires_at) WHERE status = 'open';

CREATE TABLE reviews (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_id BIGINT NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    answer TEXT NOT NULL,
    correct BOOLEAN NOT NULL,
    quality INTEGER NOT NULL CHECK (quality BETWEEN 0 AND 5),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_reviews_user_created ON reviews(user_id, created_at DESC);
CREATE INDEX idx_reviews_word ON reviews(word_id);

CREATE TABLE pending_cards (
    id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    language TEXT NOT NULL,
    deck_name TEXT NOT NULL,
    cards JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'committed', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE curator_plans (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_date DATE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('plan', 'digest')),
    plan JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, run_date, kind)
);

CREATE TABLE curator_runs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('plan', 'digest')),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX idx_curator_runs_latest ON curator_runs(user_id, kind, started_at DESC);

CREATE TABLE llm_usage (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    month DATE NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved', 'reconciled', 'released')),
    reserved_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    actual_usd NUMERIC(12, 6),
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reconciled_at TIMESTAMPTZ
);

CREATE INDEX idx_llm_usage_month ON llm_usage(user_id, month);

CREATE TABLE deliveries (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('push', 'digest')),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'claimed' CHECK (status IN ('claimed', 'sent', 'failed', 'released')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    telegram_message_id BIGINT,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    error TEXT
);

CREATE INDEX idx_deliveries_user_claimed ON deliveries(user_id, claimed_at DESC);
