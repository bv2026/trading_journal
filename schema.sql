CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,
    broker       TEXT NOT NULL,
    account_type TEXT DEFAULT 'investment',
    holder       TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id           TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    date         DATE NOT NULL,
    category     TEXT NOT NULL,   -- cash_flow | dividend | margin_interest | fee | reward | other
    subcategory  TEXT,
    amount       REAL NOT NULL,   -- positive = inflow, negative = outflow (USD)
    currency     TEXT DEFAULT 'USD',
    symbol       TEXT,
    description  TEXT,
    source_file  TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_account  ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);

CREATE TABLE IF NOT EXISTS positions (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    ticker       TEXT NOT NULL,
    name         TEXT,
    shares       REAL,
    cost_basis   REAL,
    sector       TEXT,
    industry     TEXT,
    asset_type   TEXT,
    iv_rank      REAL,
    perf_ytd     REAL,
    atr_pct      REAL,
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, ticker)
);
