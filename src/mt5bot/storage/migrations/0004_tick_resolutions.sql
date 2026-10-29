-- 0004: which level (stop or target) real ticks touched first, from the MQL5 observer EA or Python tick data.
CREATE TABLE tick_resolutions (
    decision_id    INTEGER PRIMARY KEY REFERENCES decisions(id) ON DELETE CASCADE,
    source         TEXT NOT NULL CHECK (source IN ('MT5_EA', 'PY_TICKS')),
    entry_time_utc TEXT,
    entry_price    REAL,
    first_hit      TEXT NOT NULL CHECK (first_hit IN ('TP', 'SL', 'NONE', 'LATE')),
    hit_time_utc   TEXT,
    hit_price      REAL,
    ticks_seen     INTEGER NOT NULL DEFAULT 0,
    imported_at    TEXT NOT NULL
);
CREATE INDEX ix_quotes_time ON quotes(time_utc);
