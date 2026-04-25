CREATE TABLE IF NOT EXISTS accounts (
    account_id     TEXT PRIMARY KEY,
    broker         TEXT NOT NULL,
    account_type   TEXT DEFAULT 'equity',
                   -- equity | options | futures | crypto
    account_group  TEXT DEFAULT 'investment',
                   -- investment | retirement
    holder         TEXT,
    price_source   TEXT DEFAULT 'live',
                   -- live (yfinance) | static (stored in DB)
    active         INTEGER DEFAULT 1
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
    account_id    TEXT NOT NULL REFERENCES accounts(account_id),
    ticker        TEXT NOT NULL,
    name          TEXT,
    shares        REAL,
    cost_basis    REAL,
    stored_price  REAL,   -- CSV price for static accounts; NULL for live-priced accounts
    sector        TEXT,
    industry      TEXT,
    asset_type    TEXT,
    iv_rank       REAL,
    perf_ytd      REAL,
    atr_pct       REAL,
    source_file   TEXT,
    ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, ticker)
);

CREATE TABLE IF NOT EXISTS options_positions (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    symbol       TEXT NOT NULL,
    underlying   TEXT,
    expiry       TEXT,
    strike       REAL,
    call_put     TEXT,
    description  TEXT,
    qty          REAL,
    price        REAL,
    market_value REAL,
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_opt_account    ON options_positions(account_id);
CREATE INDEX IF NOT EXISTS idx_opt_underlying ON options_positions(underlying);
CREATE INDEX IF NOT EXISTS idx_opt_expiry     ON options_positions(expiry);

CREATE TABLE IF NOT EXISTS futures_positions (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    symbol       TEXT NOT NULL,
    underlying   TEXT,
    description  TEXT,
    qty          REAL,
    price        REAL,
    market_value REAL,
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_fut_account ON futures_positions(account_id);

CREATE TABLE IF NOT EXISTS crypto_positions (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    symbol       TEXT NOT NULL,
    name         TEXT,
    qty          REAL,
    price        REAL,
    cost_basis   REAL,
    market_value REAL,
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_crypto_account ON crypto_positions(account_id);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_date  TEXT NOT NULL,
    account_id     TEXT NOT NULL REFERENCES accounts(account_id),
    market_value   REAL NOT NULL,
    cost_basis     REAL,
    margin         REAL DEFAULT 0.0,
    PRIMARY KEY (snapshot_date, account_id)
);

CREATE INDEX IF NOT EXISTS idx_snap_date    ON portfolio_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_snap_account ON portfolio_snapshots(account_id);

-- ── v_positions_all ──────────────────────────────────────────────────────────
-- Unified view across all 4 position tables. equity market_value is NULL here
-- because it requires a live price fetch; all other asset classes store it.
CREATE VIEW IF NOT EXISTS v_positions_all AS
    SELECT account_id, ticker AS symbol, 'equity' AS asset_class,
           NULL AS underlying, NULL AS expiry, NULL AS strike, NULL AS call_put,
           shares AS qty, cost_basis AS unit_cost, NULL AS market_value,
           name, sector, industry
    FROM positions
    UNION ALL
    SELECT account_id, symbol, 'options',
           underlying, expiry, strike, call_put,
           qty, price, market_value, description, NULL, NULL
    FROM options_positions
    UNION ALL
    SELECT account_id, symbol, 'futures',
           underlying, NULL, NULL, NULL,
           qty, price, market_value, description, NULL, NULL
    FROM futures_positions
    UNION ALL
    SELECT account_id, symbol, 'crypto',
           NULL, NULL, NULL, NULL,
           qty, price, market_value, name, NULL, NULL
    FROM crypto_positions;

-- ── v_transaction_summary ────────────────────────────────────────────────────
-- Transaction totals per account per year.
CREATE VIEW IF NOT EXISTS v_transaction_summary AS
    SELECT
        account_id,
        strftime('%Y', date) AS year,
        SUM(CASE WHEN category='cash_flow' AND subcategory='deposit'    THEN amount ELSE 0 END) AS deposits,
        SUM(CASE WHEN category='cash_flow' AND subcategory='withdrawal' THEN amount ELSE 0 END) AS withdrawals,
        SUM(CASE WHEN category='cash_flow'                              THEN amount ELSE 0 END) AS net_cash_flow,
        SUM(CASE WHEN category='dividend'                               THEN amount ELSE 0 END) AS dividends,
        SUM(CASE WHEN category='reward'                                 THEN amount ELSE 0 END) AS rewards,
        SUM(CASE WHEN category IN ('dividend','reward')                 THEN amount ELSE 0 END) AS div_plus_rewards,
        SUM(CASE WHEN category='margin_interest'                        THEN amount ELSE 0 END) AS margin_interest,
        SUM(CASE WHEN category='fee'                                    THEN amount ELSE 0 END) AS fees,
        SUM(CASE WHEN category NOT IN ('cash_flow','other')             THEN amount ELSE 0 END) AS net_income
    FROM transactions
    GROUP BY account_id, year;

-- ── v_yearly_summary ─────────────────────────────────────────────────────────
-- Yearly rollup across all accounts.
CREATE VIEW IF NOT EXISTS v_yearly_summary AS
    SELECT year,
        SUM(deposits) AS deposits, SUM(withdrawals) AS withdrawals,
        SUM(net_cash_flow) AS net_cash_flow,
        SUM(dividends) AS dividends, SUM(rewards) AS rewards,
        SUM(div_plus_rewards) AS div_plus_rewards,
        SUM(margin_interest) AS margin_interest,
        SUM(fees) AS fees, SUM(net_income) AS net_income
    FROM v_transaction_summary
    GROUP BY year;

-- ── v_snapshot_latest ────────────────────────────────────────────────────────
-- Most recent snapshot per account.
CREATE VIEW IF NOT EXISTS v_snapshot_latest AS
    SELECT s.account_id, s.market_value, s.cost_basis, s.margin, s.snapshot_date
    FROM portfolio_snapshots s
    INNER JOIN (
        SELECT account_id, MAX(snapshot_date) AS max_date
        FROM portfolio_snapshots GROUP BY account_id
    ) latest ON s.account_id = latest.account_id
            AND s.snapshot_date = latest.max_date;

-- ── v_snapshot_periods ───────────────────────────────────────────────────────
-- Per-account market value at standard lookback periods.
-- NULL = no snapshot exists for that period yet.
CREATE VIEW IF NOT EXISTS v_snapshot_periods AS
    SELECT
        cur.account_id,
        cur.snapshot_date AS current_date,
        cur.market_value  AS current_value,
        w1.market_value   AS value_1w,
        m1.market_value   AS value_1m,
        m3.market_value   AS value_3m,
        m12.market_value  AS value_1y,
        ytd.market_value  AS value_ytd_start
    FROM v_snapshot_latest cur
    LEFT JOIN portfolio_snapshots w1
        ON w1.account_id = cur.account_id
        AND w1.snapshot_date = (
            SELECT MAX(snapshot_date) FROM portfolio_snapshots
            WHERE account_id = cur.account_id
              AND snapshot_date <= date(cur.snapshot_date, '-7 days'))
    LEFT JOIN portfolio_snapshots m1
        ON m1.account_id = cur.account_id
        AND m1.snapshot_date = (
            SELECT MAX(snapshot_date) FROM portfolio_snapshots
            WHERE account_id = cur.account_id
              AND snapshot_date <= date(cur.snapshot_date, '-30 days'))
    LEFT JOIN portfolio_snapshots m3
        ON m3.account_id = cur.account_id
        AND m3.snapshot_date = (
            SELECT MAX(snapshot_date) FROM portfolio_snapshots
            WHERE account_id = cur.account_id
              AND snapshot_date <= date(cur.snapshot_date, '-90 days'))
    LEFT JOIN portfolio_snapshots m12
        ON m12.account_id = cur.account_id
        AND m12.snapshot_date = (
            SELECT MAX(snapshot_date) FROM portfolio_snapshots
            WHERE account_id = cur.account_id
              AND snapshot_date <= date(cur.snapshot_date, '-365 days'))
    LEFT JOIN portfolio_snapshots ytd
        ON ytd.account_id = cur.account_id
        AND ytd.snapshot_date = (
            SELECT MAX(snapshot_date) FROM portfolio_snapshots
            WHERE account_id = cur.account_id
              AND snapshot_date <= (strftime('%Y', cur.snapshot_date) || '-01-01'));
