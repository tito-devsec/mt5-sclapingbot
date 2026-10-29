-- 0002: broker contract specifications captured from symbol metadata.
CREATE TABLE instrument_specs (
    symbol        TEXT PRIMARY KEY,
    broker_symbol TEXT NOT NULL,
    base          TEXT NOT NULL,
    quote         TEXT NOT NULL,
    point         REAL NOT NULL CHECK (point > 0),
    digits        INTEGER NOT NULL,
    contract_size REAL NOT NULL CHECK (contract_size > 0),
    volume_min    REAL NOT NULL,
    volume_step   REAL NOT NULL,
    volume_max    REAL NOT NULL,
    source        TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
