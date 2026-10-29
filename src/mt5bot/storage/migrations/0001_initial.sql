-- 0001: initial schema. All timestamps are ISO-8601 UTC text ("YYYY-MM-DDTHH:MM:SS+00:00").

-- ---------------- raw observations ----------------
CREATE TABLE ingestion_runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    source          TEXT NOT NULL,              -- e.g. 'mt5', 'fixture'
    symbol          TEXT NOT NULL,
    broker_symbol   TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    requested_start TEXT NOT NULL,
    requested_end   TEXT NOT NULL,
    bars_received   INTEGER NOT NULL DEFAULT 0,
    bars_inserted   INTEGER NOT NULL DEFAULT 0,
    bars_rejected   INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL CHECK (status IN ('RUNNING','OK','PARTIAL','FAILED')),
    error_code      TEXT,
    error_message   TEXT
);

CREATE TABLE bars (
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    time_utc       TEXT NOT NULL,               -- bar OPEN time
    open           REAL NOT NULL CHECK (open > 0),
    high           REAL NOT NULL CHECK (high > 0),
    low            REAL NOT NULL CHECK (low > 0),
    close          REAL NOT NULL CHECK (close > 0),
    tick_volume    INTEGER NOT NULL CHECK (tick_volume >= 0),  -- broker tick count, not traded volume
    spread_points  INTEGER NOT NULL CHECK (spread_points >= 0),
    real_volume    INTEGER NOT NULL DEFAULT 0,
    source         TEXT NOT NULL,
    broker_symbol  TEXT NOT NULL,
    ingestion_run  INTEGER REFERENCES ingestion_runs(id),
    collected_at   TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe, time_utc),
    CHECK (high >= low AND high >= open AND high >= close AND low <= open AND low <= close)
) WITHOUT ROWID;

CREATE TABLE ingestion_checkpoints (
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    last_bar_utc   TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE quotes (
    symbol        TEXT NOT NULL,
    time_utc      TEXT NOT NULL,
    bid           REAL NOT NULL CHECK (bid > 0),
    ask           REAL NOT NULL CHECK (ask > 0),
    source        TEXT NOT NULL,
    collected_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, time_utc),
    CHECK (ask >= bid)
) WITHOUT ROWID;

CREATE TABLE data_quality_issues (
    id             INTEGER PRIMARY KEY,
    detected_at    TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    timeframe      TEXT,
    time_utc       TEXT,
    issue          TEXT NOT NULL,
    severity       TEXT NOT NULL CHECK (severity IN ('INFO','WARNING','ERROR')),
    detail         TEXT,
    ingestion_run  INTEGER REFERENCES ingestion_runs(id),
    UNIQUE (symbol, timeframe, time_utc, issue)
);
CREATE INDEX ix_dq_symbol ON data_quality_issues(symbol, timeframe, detected_at);

-- ---------------- news ----------------
CREATE TABLE news_items (
    id             INTEGER PRIMARY KEY,
    fingerprint    TEXT NOT NULL UNIQUE,
    provider_id    TEXT NOT NULL,
    source_name    TEXT NOT NULL,
    provider_item_id TEXT,
    headline       TEXT NOT NULL,
    summary        TEXT,                        -- only where provider terms allow storing it
    url            TEXT,
    published_at   TEXT,                        -- as stated by the source (may be NULL)
    retrieved_at   TEXT NOT NULL,               -- first time we saw it
    available_at   TEXT NOT NULL,               -- max(published_at, retrieved_at): usable from here
    category       TEXT NOT NULL,
    currencies     TEXT NOT NULL,               -- JSON list
    importance     TEXT,                        -- only if the source supplies it
    sentiment      REAL,
    quality_status TEXT NOT NULL CHECK (quality_status IN ('OK','NO_PUBLISH_TIME','FUTURE_TIMESTAMP'))
);
CREATE INDEX ix_news_available ON news_items(available_at);

CREATE TABLE economic_events (
    id             INTEGER PRIMARY KEY,
    fingerprint    TEXT NOT NULL UNIQUE,
    source         TEXT NOT NULL,
    scheduled_at   TEXT NOT NULL,
    currency       TEXT NOT NULL,
    title          TEXT NOT NULL,
    category       TEXT,
    importance     TEXT CHECK (importance IN ('LOW','MEDIUM','HIGH') OR importance IS NULL),
    actual         TEXT,
    forecast       TEXT,
    previous       TEXT,
    published_at   TEXT,
    retrieved_at   TEXT NOT NULL
);
CREATE INDEX ix_events_time ON economic_events(scheduled_at, currency);

CREATE TABLE provider_status (
    provider_id    TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_error     TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    items_last_fetch INTEGER NOT NULL DEFAULT 0
);

-- ---------------- derived ----------------
CREATE TABLE feature_sets (
    hash           TEXT PRIMARY KEY,
    version        TEXT NOT NULL,
    definition     TEXT NOT NULL,               -- JSON
    created_at     TEXT NOT NULL
);

CREATE TABLE feature_snapshots (
    id             INTEGER PRIMARY KEY,
    feature_set    TEXT NOT NULL REFERENCES feature_sets(hash),
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    bar_time_utc   TEXT NOT NULL,
    available_at   TEXT NOT NULL,
    values_json    TEXT NOT NULL,
    UNIQUE (feature_set, symbol, timeframe, bar_time_utc)
);

-- ---------------- ML ----------------
CREATE TABLE experiments (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    kind           TEXT NOT NULL,               -- 'train','evaluate','backtest','tune'
    config_hash    TEXT NOT NULL,
    code_version   TEXT,
    data_fingerprint TEXT,
    params_json    TEXT NOT NULL,
    n_trials       INTEGER NOT NULL DEFAULT 1,
    notes          TEXT
);

CREATE TABLE model_versions (
    id             TEXT PRIMARY KEY,            -- e.g. 'meta-lightgbm-20261029T101500Z-ab12cd'
    kind           TEXT NOT NULL,               -- 'meta_label','regime'
    model_type     TEXT NOT NULL,
    artifact_path  TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    feature_set    TEXT NOT NULL,
    feature_names  TEXT NOT NULL,               -- JSON list
    train_start    TEXT NOT NULL,
    train_end      TEXT NOT NULL,
    dataset_fingerprint TEXT NOT NULL,
    params_json    TEXT NOT NULL,
    metrics_json   TEXT NOT NULL,
    seed           INTEGER NOT NULL,
    library_versions TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('CHALLENGER','CHAMPION','RETIRED','REJECTED')),
    created_at     TEXT NOT NULL,
    experiment_id  INTEGER REFERENCES experiments(id)
);
CREATE UNIQUE INDEX ux_one_champion ON model_versions(kind) WHERE status = 'CHAMPION';

CREATE TABLE evaluation_runs (
    id             INTEGER PRIMARY KEY,
    model_id       TEXT REFERENCES model_versions(id),
    created_at     TEXT NOT NULL,
    split          TEXT NOT NULL,               -- 'validation','test','walk_forward'
    period_start   TEXT NOT NULL,
    period_end     TEXT NOT NULL,
    metrics_json   TEXT NOT NULL
);

CREATE TABLE promotions (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    kind           TEXT NOT NULL,
    action         TEXT NOT NULL CHECK (action IN ('PROMOTE','REJECT','ROLLBACK')),
    model_id       TEXT NOT NULL REFERENCES model_versions(id),
    previous_champion TEXT REFERENCES model_versions(id),
    reason         TEXT NOT NULL,
    gates_json     TEXT NOT NULL
);

-- ---------------- predictions and decisions ----------------
CREATE TABLE decisions (
    id             INTEGER PRIMARY KEY,
    decided_at     TEXT NOT NULL,               -- wall-clock time of the decision (UTC)
    bar_time_utc   TEXT NOT NULL,               -- open time of the last closed bar used
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    mode           TEXT NOT NULL CHECK (mode IN ('HISTORICAL','SIMULATED','OBSERVED_DEMO')),
    status         TEXT NOT NULL CHECK (status IN ('RECOMMEND','NO_TRADE','UNCERTAIN','REJECTED')),
    regime         TEXT NOT NULL,
    regime_confidence REAL,
    feature_set    TEXT,
    features_json  TEXT,
    news_ids_json  TEXT NOT NULL DEFAULT '[]',
    selected_strategy TEXT,
    strategy_version TEXT,
    direction      TEXT CHECK (direction IN ('LONG','SHORT') OR direction IS NULL),
    entry_ref      REAL,
    stop_distance  REAL,
    target_distance REAL,
    max_hold_bars  INTEGER,
    est_cost_r     REAL,
    rationale      TEXT NOT NULL,
    model_versions_json TEXT NOT NULL DEFAULT '{}',
    config_hash    TEXT NOT NULL,
    eval_status    TEXT NOT NULL DEFAULT 'PENDING' CHECK (eval_status IN ('PENDING','DONE','NOT_APPLICABLE')),
    UNIQUE (symbol, timeframe, bar_time_utc, mode)
);
CREATE INDEX ix_decisions_eval ON decisions(eval_status, decided_at);

CREATE TABLE regime_predictions (
    id             INTEGER PRIMARY KEY,
    decision_id    INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    model          TEXT NOT NULL,
    regime         TEXT NOT NULL,
    confidence     REAL
);

CREATE TABLE strategy_candidates (
    id             INTEGER PRIMARY KEY,
    decision_id    INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    strategy       TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    direction      TEXT NOT NULL,
    eligible       INTEGER NOT NULL,
    eligibility_reason TEXT,
    probability    REAL,
    lcb_reward     REAL,
    score          REAL,
    sample_size    INTEGER
);

CREATE TABLE risk_evaluations (
    id             INTEGER PRIMARY KEY,
    decision_id    INTEGER REFERENCES decisions(id) ON DELETE CASCADE,
    evaluated_at   TEXT NOT NULL,
    check_code     TEXT NOT NULL,
    passed         INTEGER NOT NULL,
    detail         TEXT
);
CREATE INDEX ix_risk_decision ON risk_evaluations(decision_id);

CREATE TABLE decision_outcomes (
    decision_id    INTEGER PRIMARY KEY REFERENCES decisions(id) ON DELETE CASCADE,
    evaluated_at   TEXT NOT NULL,
    label_version  TEXT NOT NULL,
    exit_reason    TEXT,
    exit_time_utc  TEXT,
    gross_r        REAL,
    net_r          REAL,
    mae_r          REAL,
    mfe_r          REAL,
    reward         REAL,
    attribution    TEXT NOT NULL,
    evidence_json  TEXT NOT NULL
);

CREATE TABLE strategy_performance (
    bucket_key     TEXT PRIMARY KEY,            -- JSON of the context dimensions
    strategy       TEXT NOT NULL,
    n              INTEGER NOT NULL,
    mean_reward    REAL NOT NULL,
    m2_reward      REAL NOT NULL,               -- Welford sum of squares
    wins           INTEGER NOT NULL,
    updated_at     TEXT NOT NULL
);

-- ---------------- simulation ----------------
CREATE TABLE backtest_runs (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    experiment_id  INTEGER REFERENCES experiments(id),
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    period_start   TEXT NOT NULL,
    period_end     TEXT NOT NULL,
    strategies     TEXT NOT NULL,
    cost_multiplier REAL NOT NULL DEFAULT 1.0,
    config_json    TEXT NOT NULL,
    metrics_json   TEXT NOT NULL
);

CREATE TABLE simulated_trades (
    id             INTEGER PRIMARY KEY,
    backtest_run   INTEGER NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    strategy       TEXT NOT NULL,
    direction      TEXT NOT NULL,
    signal_time    TEXT NOT NULL,
    entry_time     TEXT NOT NULL,
    entry_price    REAL NOT NULL,
    exit_time      TEXT NOT NULL,
    exit_price     REAL NOT NULL,
    exit_reason    TEXT NOT NULL,
    lots           REAL NOT NULL,
    gross_pnl_usd  REAL NOT NULL,
    cost_usd       REAL NOT NULL,
    net_pnl_usd    REAL NOT NULL,
    net_r          REAL NOT NULL,
    regime         TEXT
);

-- ---------------- monitoring ----------------
CREATE TABLE drift_alerts (
    id             INTEGER PRIMARY KEY,
    detected_at    TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('INPUT_DRIFT','REGIME_CHANGE','PREDICTION_QUALITY','STRATEGY_PERFORMANCE','FEED_FAILURE','EXECUTION_COST')),
    severity       TEXT NOT NULL CHECK (severity IN ('INFO','WARNING','CRITICAL')),
    scope          TEXT NOT NULL,               -- symbol/timeframe/strategy/feature
    metric         TEXT NOT NULL,
    value          REAL,
    threshold      REAL,
    evidence_json  TEXT NOT NULL,
    response       TEXT NOT NULL,
    resolved_at    TEXT
);
CREATE INDEX ix_drift_open ON drift_alerts(resolved_at, kind);

CREATE TABLE strategy_status (
    strategy       TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('ACTIVE','SUSPENDED')),
    reason         TEXT,
    changed_at     TEXT NOT NULL,
    PRIMARY KEY (strategy, symbol, timeframe)
);

CREATE TABLE llm_requests (
    id             INTEGER PRIMARY KEY,
    requested_at   TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    task           TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    cache_key      TEXT NOT NULL,
    status         TEXT NOT NULL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    response_json  TEXT
);
CREATE INDEX ix_llm_cache ON llm_requests(cache_key, status);
