-- 0005: agent memory: skills, episodes, vectors (SQLite backend), lessons, playbook cache and imported user trades.
CREATE TABLE skills (
    id               INTEGER PRIMARY KEY,
    technique        TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    timeframe        TEXT NOT NULL,
    regime           TEXT NOT NULL,
    session          TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('EXPERIMENTAL','PROBATION','VALIDATED','RETIRED')),
    n                INTEGER NOT NULL DEFAULT 0,
    mean_reward      REAL NOT NULL DEFAULT 0,
    m2_reward        REAL NOT NULL DEFAULT 0,
    wins             INTEGER NOT NULL DEFAULT 0,
    stage_n          INTEGER NOT NULL DEFAULT 0,
    stage_mean       REAL NOT NULL DEFAULT 0,
    stage_m2         REAL NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    state_changed_at TEXT NOT NULL,
    UNIQUE (technique, symbol, timeframe, regime, session)
);
CREATE INDEX ix_skills_state ON skills(state, symbol, timeframe);

CREATE TABLE skill_events (
    id         INTEGER PRIMARY KEY,
    skill_id   INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    at         TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    reason     TEXT NOT NULL
);

CREATE TABLE episodes (
    id            INTEGER PRIMARY KEY,
    candidate_id  INTEGER UNIQUE REFERENCES strategy_candidates(id) ON DELETE SET NULL,
    user_ticket   INTEGER UNIQUE,
    source        TEXT NOT NULL CHECK (source IN ('AGENT','USER')),
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    technique     TEXT NOT NULL,
    direction     TEXT NOT NULL,
    regime        TEXT NOT NULL,
    session       TEXT NOT NULL,
    decided_at    TEXT NOT NULL,
    available_at  TEXT NOT NULL,
    net_r         REAL NOT NULL,
    reward        REAL NOT NULL,
    reward_risk   REAL NOT NULL DEFAULT 1.0,
    features_json TEXT NOT NULL
);
CREATE INDEX ix_episodes_available ON episodes(available_at);

CREATE TABLE memory_vectors (
    collection    TEXT NOT NULL,
    item_id       INTEGER NOT NULL,
    dim           INTEGER NOT NULL,
    vector        BLOB NOT NULL,
    metadata_json TEXT NOT NULL,
    available_at  TEXT NOT NULL,
    PRIMARY KEY (collection, item_id)
);

CREATE TABLE lessons (
    id            INTEGER PRIMARY KEY,
    day           TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    text          TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    UNIQUE (day, kind, text)
);

CREATE TABLE playbook_cache (
    id           INTEGER PRIMARY KEY,
    built_at     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_json TEXT NOT NULL
);

CREATE TABLE user_trades (
    ticket       INTEGER PRIMARY KEY,
    symbol       TEXT NOT NULL,
    direction    TEXT NOT NULL,
    entry_time   TEXT NOT NULL,
    exit_time    TEXT NOT NULL,
    entry_price  REAL NOT NULL,
    exit_price   REAL NOT NULL,
    volume       REAL NOT NULL,
    profit_usd   REAL NOT NULL,
    imported_at  TEXT NOT NULL
);
