-- 0003: store candidate trade geometry and counterfactual outcomes so memory is not selection-biased.
ALTER TABLE strategy_candidates ADD COLUMN rule_eligible INTEGER NOT NULL DEFAULT 0;
ALTER TABLE strategy_candidates ADD COLUMN stop_distance REAL;
ALTER TABLE strategy_candidates ADD COLUMN target_distance REAL;
ALTER TABLE strategy_candidates ADD COLUMN max_hold_bars INTEGER;

CREATE TABLE candidate_outcomes (
    candidate_id   INTEGER PRIMARY KEY REFERENCES strategy_candidates(id) ON DELETE CASCADE,
    evaluated_at   TEXT NOT NULL,
    label_version  TEXT NOT NULL,
    provenance     TEXT NOT NULL CHECK (provenance IN ('SIMULATED')),
    exit_reason    TEXT,
    exit_time_utc  TEXT,
    net_r          REAL,
    mae_r          REAL,
    reward         REAL
);
