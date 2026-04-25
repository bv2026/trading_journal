# Trading Journal — Architecture & Design

## Overview

Personal portfolio tracker consolidating brokerage activity and live positions
across multiple accounts into a single SQLite database with a Streamlit dashboard
and Claude Desktop MCP integration.

---

## Current Architecture (v1)

```
activity/                   Broker CSV exports (gitignored)
data/journal.db             SQLite — transactions + equity positions
src/
  db.py                     DB helpers: init, upsert, insert, load
  metrics.py                compute_metrics, style helpers
  positions.py              load_positions_from_db, live price fetch (yfinance)
  parsers/
    utils.py                parse_amount, parse_date, make_id
    positions_csv.py        per-account equity positions parser
    robinhood.py
    webull.py
    tradestation.py
    schwab.py
    tradier.py
    coinbase.py
    fidelity.py             yearly summary parser (2020+)
dashboard/app.py            Streamlit — 5 tabs
mcp_server.py               FastMCP server (Claude Desktop)
ingest.py                   Orchestrates CSV → DB pipeline
schema.sql                  Table definitions
tests/
  unit/                     Parser-level unit tests (~95 tests)
  integration/              DB round-trip integration tests (~50 tests)
```

### Current DB schema

```sql
accounts      (account_id PK, broker, account_type, holder)
transactions  (id PK, account_id FK, date, category, subcategory, amount,
               currency, symbol, description, source_file, created_at)
positions     (account_id+ticker PK, name, shares, cost_basis, sector,
               industry, asset_type, iv_rank, perf_ytd, atr_pct,
               source_file, ingested_at)
```

### Ingest flow

```
python ingest.py
  ├── init_db()                       create tables if missing
  ├── upsert_accounts()               register account metadata
  ├── for each PARSERS entry:
  │     parse CSV → list[dict]
  │     insert_transactions()         INSERT OR IGNORE (content-based IDs)
  └── for each POSITION_FILES entry:
        parse positions CSV
        delete_positions_by_account()
        insert_positions()            INSERT OR REPLACE
```

### Dashboard tabs (current)

| # | Tab | Contents |
|---|-----|----------|
| 1 | Portfolio | Net worth banner, account summary, sector pies, positions by account, sector summary, yearly pivots, crypto flow |
| 2 | Yearly Summary | YoY table, income/cost/cash-flow/dividend charts |
| 3 | By Account | Prev Year / Current Year / ALL pivot tables per account |
| 4 | Positions | Holdings by symbol — MV, Cost, P&L, Sector, Return%, Dividends |
| 5 | Transactions | Filterable log with CSV export |

### Pricing model (current)

Equity positions only. Live prices fetched from yfinance at dashboard load time:
- `_fetch_live_prices(tickers)` → `{ticker: price}` dict
- `MARKET VALUE = Shares × PRICE`
- `COST = Shares × Cost_Basis`
- `totalReturn = MARKET VALUE − COST`
- MARGIN rows: `MARKET VALUE = cost_basis` (no yfinance lookup)
- Prices cached 5 minutes in dashboard session

---

## Target Architecture (v2)

### Goals

1. Track options, futures, and crypto positions alongside equity (separate tables)
2. Persist daily portfolio value snapshots to enable historical performance reporting
3. Add a Performance tab (tab 6) showing week/month/quarter/YTD/year returns per account
4. Lay the groundwork for retirement accounts (future phase — not implemented now)
5. Add a SQL reporting layer (views) so aggregations can be queried directly from the DB

### What does NOT change

- Transaction ingest pipeline — untouched
- Equity positions table and parser — untouched
- Dashboard tabs 1–5 — untouched
- MCP tools (except `get_positions` gains richer data)

---

## v2 DB Schema

### `accounts` table — extended

```sql
CREATE TABLE IF NOT EXISTS accounts (
    account_id     TEXT PRIMARY KEY,
    broker         TEXT NOT NULL,
    account_type   TEXT DEFAULT 'equity',
                   -- equity | options | futures | crypto
    account_group  TEXT DEFAULT 'investment',
                   -- investment | retirement  (retirement = future phase)
    holder         TEXT,
                   -- BV | KD | null
    price_source   TEXT DEFAULT 'live',
                   -- live (yfinance) | static (stored in CSV/DB)
    active         INTEGER DEFAULT 1
);
```

`account_type` drives which positions table is written and read.
`price_source` drives how market value is computed at runtime:
- `live` → yfinance fetch at dashboard load (equity only)
- `static` → price stored in DB at ingest time (options, futures, crypto)

### `positions` table — unchanged

```sql
-- No changes to this table or its parser.
positions (account_id+ticker PK, name, shares, cost_basis, sector,
           industry, asset_type, iv_rank, perf_ytd, atr_pct,
           source_file, ingested_at)
```

### `options_positions` table — new

```sql
CREATE TABLE IF NOT EXISTS options_positions (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    symbol       TEXT NOT NULL,   -- full OCC symbol e.g. "GOOGL 20270115C  360.000"
    underlying   TEXT,            -- GOOGL, QQQ, SPX
    expiry       TEXT,            -- YYYY-MM-DD
    strike       REAL,
    call_put     TEXT,            -- Call | Put
    description  TEXT,
    qty          REAL,            -- contracts; negative = short
    price        REAL,            -- option price per share (× 100 = contract value)
    market_value REAL,            -- qty × price × 100  (already computed in CSV)
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_opt_account    ON options_positions(account_id);
CREATE INDEX IF NOT EXISTS idx_opt_underlying ON options_positions(underlying);
CREATE INDEX IF NOT EXISTS idx_opt_expiry     ON options_positions(expiry);
```

### `futures_positions` table — new

```sql
CREATE TABLE IF NOT EXISTS futures_positions (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    symbol       TEXT NOT NULL,   -- /ESM25, /CLM25 etc.
    underlying   TEXT,
    description  TEXT,
    qty          REAL,            -- negative = short
    price        REAL,
    market_value REAL,
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_fut_account ON futures_positions(account_id);
```

### `crypto_positions` table — new

```sql
CREATE TABLE IF NOT EXISTS crypto_positions (
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    symbol       TEXT NOT NULL,   -- BTC, ETH etc.
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
```

### `portfolio_snapshots` table — new

```sql
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_date  TEXT NOT NULL,   -- YYYY-MM-DD (date of ingest run)
    account_id     TEXT NOT NULL REFERENCES accounts(account_id),
    market_value   REAL NOT NULL,   -- sum of all positions in this account
    cost_basis     REAL,            -- total cost at snapshot time
    margin         REAL DEFAULT 0.0,
    PRIMARY KEY (snapshot_date, account_id)
);

CREATE INDEX IF NOT EXISTS idx_snap_date    ON portfolio_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_snap_account ON portfolio_snapshots(account_id);
```

Written at the end of every `ingest.py` run via `INSERT OR REPLACE` — same-day
re-runs update the row rather than creating duplicates. Equity market values
are computed after yfinance price fetch; static asset values come directly from
the positions tables.

---

## SQL Reporting Layer (Views)

Views provide a stable query interface for the dashboard, MCP tools, and any
external BI tools connecting directly to the SQLite file.

```sql
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
-- Transaction totals per account per year — base for all financial reporting.
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
-- Yearly rollup across all accounts (feeds Yearly Summary tab).
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
-- Python layer computes % returns from these raw values.
-- NULL = no snapshot exists for that period yet (accumulates over time).
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
```

---

## Account Naming Convention

```
{BROKER}-{HOLDER}[-{TYPE}]
```

- Suffix-free accounts (e.g. `RH-BV`, `SCHWAB`) are always equity — backward compatible.
- Type suffix is appended only for non-equity accounts.

| account_id | broker | account_type | price_source | holder | account_group |
|---|---|---|---|---|---|
| `RH-BV` | robinhood | equity | live | BV | investment |
| `RH-KD` | robinhood | equity | live | KD | investment |
| `RH-BV-OPT` | robinhood | options | static | BV | investment |
| `RH-BV-FUT` | robinhood | futures | static | BV | investment |
| `RH-BV-CRYPTO` | robinhood | crypto | static | BV | investment |
| `SCHWAB` | schwab | equity | live | BV | investment |
| `SCHWAB-OPT` | schwab | options | static | BV | investment |
| `TRADIER` | tradier | equity | live | BV | investment |
| `TRADIER-OPT` | tradier | options | static | BV | investment |
| `WEBULL` | webull | equity | live | BV | investment |
| `TS` | tradestation | equity | live | BV | investment |
| `COINBASE` | coinbase | crypto | static | BV | investment |
| `FIDELITY` | fidelity | equity | live | BV | investment |
| *(future)* `RET-BV-401K` | fidelity | equity | live | BV | retirement |
| *(future)* `RET-BV-IRA` | fidelity | equity | live | BV | retirement |
| *(future)* `RET-BV-ROTH` | fidelity | equity | live | BV | retirement |
| *(future)* `RET-BV-HSA` | fidelity | equity | live | BV | retirement |
| *(future)* `RET-KD-IRA` | fidelity | equity | live | KD | retirement |
| *(future)* `RET-KD-ROTH` | fidelity | equity | live | KD | retirement |

Add new accounts to `ACCOUNTS` in `ingest.py` and register their position files.
Not all accounts need all types (e.g. only accounts with options positions get a `-OPT` entry).

---

## CSV File Formats

### Equity positions — unchanged

File naming: `activity/positions-{suffix}.csv`

Required columns:
```
Ticker, Name, Sh/Contr, COST BASIS, sector, industry, TYPE, IV RANK, PERF YTD, ATR %
```

Ignored (computed at runtime): `PRICE`, `COST`, `MARKET VALUE`, `totalReturn`

MARGIN row: `Ticker = MARGIN`, balance in `MARKET VALUE` (e.g. `$(25,000.00)`).

### Options / futures / crypto — same format (Tradier-style export)

File naming:
- `activity/options-{suffix}.csv`
- `activity/futures-{suffix}.csv`
- `activity/crypto-{suffix}.csv`

Required columns:
```
Symbol, Expiry, Strike, Call/Put, Description, Qty, Price, Market Value, Underlying Symbol
```

Optional / ignored: `Account Type`, `Day Change`

Rules:
- Rows with non-empty `Expiry` AND non-empty `Call/Put` → options
- Rows with non-empty `Underlying Symbol` but empty `Call/Put` → futures
- Rows with no `Expiry`/`Strike`/`Call/Put` → equity (skipped in static parser)
- `Qty` is signed: negative = short position
- `Market Value` is used as stored — no multiplier applied by the parser
  (broker has already computed `Qty × Price × 100` for options)

---

## Parser Design

### Existing parsers — unchanged

`src/parsers/positions_csv.py` — equity positions only, unchanged.

### New: `src/parsers/static_positions_csv.py`

One parser handles options, futures, and crypto. Routing to the correct DB table
is determined by the `account_type` on the calling account, not by the parser itself.

```
parse(filepath, account_id, account_type) → list[dict]
```

- Reads Tradier-format CSV
- Strips equity rows (blank `Expiry` + `Call/Put`) — those belong in the equity parser
- Cleans and coerces numeric fields (`Price`, `Market Value`, `Qty`, `Strike`)
- Returns dicts shaped for the target table (`options_positions`, `futures_positions`, or `crypto_positions`)
- Missing file → empty list (same contract as `positions_csv.py`)

---

## Ingest Pipeline (v2)

```
python ingest.py [--reset]
  ├── init_db()                         create/migrate all tables + views
  ├── upsert_accounts()                 register all accounts including new types
  │
  ├── TRANSACTION INGEST (unchanged)
  │   └── for each PARSERS entry: parse → insert_transactions()
  │
  ├── EQUITY POSITIONS (unchanged)
  │   └── for each POSITION_FILES entry:
  │         parse → delete → insert_positions()
  │
  ├── STATIC POSITIONS (new)
  │   ├── for each OPTIONS_FILES entry:
  │   │     parse → delete_options_by_account() → insert_options()
  │   ├── for each FUTURES_FILES entry:
  │   │     parse → delete_futures_by_account() → insert_futures()
  │   └── for each CRYPTO_FILES entry:
  │         parse → delete_crypto_by_account() → insert_crypto()
  │
  └── SNAPSHOT (new)
        load all positions (equity via yfinance, static from DB)
        sum market_value per account_id
        write_portfolio_snapshot(date=today)   INSERT OR REPLACE
```

File registries in `ingest.py`:
```python
POSITION_FILES = [...]   # existing equity files

OPTIONS_FILES = [
    (ACTIVITY / "options-trader.csv",  "TRADIER-OPT"),
    (ACTIVITY / "options-schwab.csv",  "SCHWAB-OPT"),
    # add as CSVs become available
]

FUTURES_FILES = [
    # add when files are available
]

CRYPTO_FILES = [
    # add when files are available
]
```

Missing files are always skipped with a warning — no file is required.

---

## Runtime Position Loading (v2)

`src/positions.py` — `load_all_positions()` (new, replaces direct `load_positions_from_db()` calls for the performance tab):

```
load_all_positions()
  ├── load_positions_from_db()         equity — yfinance prices (existing)
  ├── load_options_from_db()           options — market_value from DB
  ├── load_futures_from_db()           futures — market_value from DB
  └── load_crypto_from_db()            crypto  — market_value from DB
  → concat → unified DataFrame with asset_class column
```

Existing tabs 1–5 continue using `load_positions_from_db()` (equity only).
The new Performance tab (tab 6) uses `load_all_positions()` for net worth totals.

---

## Dashboard — Tab 6: Performance

New tab showing account-level performance in two sections:

### Portfolio Summary table

| Account | Current Value | 1 Week Ago | $ Change | % Change |
|---|---|---|---|---|
| RH-BV | $206,248 | $199,536 | +$6,712 | +3.36% |
| RH-BV-OPT | $4,200 | $3,800 | +$400 | +10.5% |
| ... | | | | |
| **Total** | | | | |

Current value = from live `load_all_positions()`.
Historical values = from `v_snapshot_periods` view.

### Portfolio Returns table

| Account | 1-Week | 1-Month | 3-Month | YTD | 1-Year |
|---|---|---|---|---|---|
| RH-BV | +3.36% | +24.83% | +23.35% | +35.65% | +157.34% |
| ... | | | | | |
| **Total** | | | | | |

Return % = `(current_value − prior_value) / prior_value`.
Periods with no prior snapshot show `—` (data accumulates over time).

Color coding: green for positive, red for negative (matching existing dashboard style).

---

## MCP Tools — Changes

| Tool | Change |
|---|---|
| `get_positions` | Returns positions from all 4 tables; `asset_class` column added |
| `get_portfolio_summary` | Includes market value from all asset classes in net worth |
| `get_performance` | **New** — queries `v_snapshot_periods`, returns return table |

Existing tools (`get_transactions`, `get_yearly_summary`, `get_account_summary`, `run_ingest`, `launch_dashboard`) — unchanged.

---

## DB Migration Strategy

No destructive migration needed. All changes are additive:

1. `schema.sql` updated with new tables + views
2. `init_db()` runs `executescript(schema.sql)` — `CREATE TABLE IF NOT EXISTS` and
   `CREATE VIEW IF NOT EXISTS` are idempotent; existing tables and data untouched
3. `accounts` table: new columns (`account_group`, `price_source`, `active`) added
   via `ALTER TABLE` migration block in `init_db()` (runs only if column is absent)
4. Existing `positions` and `transactions` tables — no schema change

Migration block in `db.py`:
```python
def _migrate(conn):
    """Add new columns to existing tables without losing data."""
    migrations = [
        "ALTER TABLE accounts ADD COLUMN account_group TEXT DEFAULT 'investment'",
        "ALTER TABLE accounts ADD COLUMN price_source  TEXT DEFAULT 'live'",
        "ALTER TABLE accounts ADD COLUMN active        INTEGER DEFAULT 1",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
```

---

## Implementation TODO

### Phase 1 — Schema & DB layer

- [ ] Update `schema.sql`:
  - Add `account_group`, `price_source`, `active` to `accounts`
  - Add `options_positions` table + indexes
  - Add `futures_positions` table + indexes
  - Add `crypto_positions` table + indexes
  - Add `portfolio_snapshots` table + indexes
  - Add all 5 views (`v_positions_all`, `v_transaction_summary`, `v_yearly_summary`, `v_snapshot_latest`, `v_snapshot_periods`)
- [ ] Update `db.py`:
  - Add `_migrate()` for new `accounts` columns
  - Call `_migrate()` inside `init_db()`
  - Update `upsert_accounts()` to include new columns
  - Add `delete_options_by_account()`, `insert_options()`
  - Add `delete_futures_by_account()`, `insert_futures()`
  - Add `delete_crypto_by_account()`, `insert_crypto()`
  - Add `load_options_db()`, `load_futures_db()`, `load_crypto_db()`
  - Add `write_portfolio_snapshot(date, account_mv_map)`
  - Add `load_snapshot_periods()` → queries `v_snapshot_periods`

### Phase 2 — Parsers

- [ ] Create `src/parsers/static_positions_csv.py`:
  - Parse Tradier-format CSV
  - Filter out equity rows (blank Expiry + Call/Put)
  - Coerce numeric fields
  - Return dicts shaped for target table (determined by `account_type` arg)
  - Unit tests in `tests/unit/test_static_positions_csv.py`

### Phase 3 — Ingest pipeline

- [ ] Update `ingest.py`:
  - Import `static_positions_csv`
  - Add new accounts to `ACCOUNTS` list (with `account_group`, `price_source`)
  - Add `OPTIONS_FILES`, `FUTURES_FILES`, `CRYPTO_FILES` registries
  - Add ingest loop for static position tables
  - Add snapshot write at end of `run()` (after all positions loaded)
- [ ] Integration tests in `tests/integration/test_static_positions_db.py`

### Phase 4 — Runtime loading

- [ ] Update `src/positions.py`:
  - Add `load_options_from_db()`, `load_futures_from_db()`, `load_crypto_from_db()`
  - Add `load_all_positions()` — concat equity + all static with `asset_class` column
  - Update `compute_net_worth()` to accept output of `load_all_positions()`

### Phase 5 — Dashboard tab 6

- [ ] Add Performance tab to `dashboard/app.py`:
  - Query `v_snapshot_periods` via `db.load_snapshot_periods()`
  - Merge with current live values from `load_all_positions()`
  - Render Portfolio Summary table (Current / 1W Ago / $ Change / % Change)
  - Render Portfolio Returns table (1W / 1M / 3M / YTD / 1Y)
  - Apply green/red color coding consistent with existing tabs

### Phase 6 — MCP updates

- [ ] Update `mcp_server.py`:
  - `get_positions` → use `load_all_positions()`, include `asset_class`
  - `get_portfolio_summary` → net worth includes all asset classes
  - Add `get_performance` tool → queries `v_snapshot_periods`

### Phase 7 — Tests & docs

- [ ] Update `tests/integration/test_positions_db.py` — verify snapshot write
- [ ] Update `USAGE.md`:
  - New account naming convention section
  - Options/futures/crypto CSV format and file naming
  - Performance tab description
  - New MCP tool `get_performance`
- [ ] Update `README.md` — 6-tab dashboard, new project structure

---

## Investment vs Retirement Account Separation

### The gate: `account_group`

`account_group` on the `accounts` table is the single source of truth.
All queries, views, and dashboard loading functions are scoped to one group at a time.
Retirement accounts are registered in the DB but invisible to the investment tabs
until a dedicated retirement section is explicitly built.

### Principle: views are unfiltered, the Python layer scopes them

The SQL views (`v_transaction_summary`, `v_yearly_summary`, `v_snapshot_periods` etc.)
cover all accounts. The Python data-loading functions accept an `account_group`
parameter and apply the filter at query time:

```python
def load_transactions(account_group: str = "investment") -> pd.DataFrame:
    # SELECT t.* FROM transactions t
    # JOIN accounts a ON t.account_id = a.account_id
    # WHERE a.account_group = ?

def load_snapshot_periods(account_group: str = "investment") -> pd.DataFrame:
    # SELECT p.* FROM v_snapshot_periods p
    # JOIN accounts a ON p.account_id = a.account_id
    # WHERE a.account_group = ?
```

This keeps views general-purpose and lets the caller decide the scope.

### Dashboard behavior

| Feature | Investment | Retirement |
|---|---|---|
| Tabs 1–5 | always shown | excluded (filtered out) |
| Tab 6 Performance | shown | excluded until retirement phase |
| Net worth banner | investment total | separate row added in retirement phase |
| MCP tools | investment by default | `account_group` param to override |

### Net worth banner — future state (retirement phase)

```
               Market Value    Margin     Net Worth
Investment     $2,100,000    $180,000    $1,920,000
Retirement     $3,100,000         —      $3,100,000
Total          $5,200,000    $180,000    $5,020,000
```

Today only the Investment row is shown (existing behavior). Retirement row added
when retirement accounts are registered.

### What retirement phase requires (future)

1. Register retirement accounts in `ACCOUNTS` with `account_group = 'retirement'`
2. Provide position CSVs — same format as equity positions
3. Add sidebar toggle or new tab to surface retirement data
4. No schema changes — the `account_group` column already handles it

---

## Future Frontend Migration (Streamlit → React)

### Compatibility assessment

The design is structured to support a future move to a Node.js/React frontend
with minimal rework to the data and business logic layers.

### What already supports migration

| Layer | Status | Notes |
|---|---|---|
| SQL views | Ready | Stable query contract any frontend can consume |
| `src/` modules | Ready | Business logic separated from presentation |
| `db.py` | Ready | All DB access in one place, no Streamlit coupling |
| MCP server | Ready | Proves `src/` is consumable by non-Streamlit clients |
| SQLite | Ready | Accessible from Node.js via `better-sqlite3` |

### What needs to be added for React

A **REST API layer** between `src/` and the frontend. The natural choice is
**FastAPI** — already adjacent to FastMCP which the project uses:

```
Current:
  Streamlit  ──→  src/  ──→  SQLite
  MCP server ──→  src/  ──→  SQLite

With FastAPI added:
  Streamlit  ──→  src/  ──→  SQLite       ← still works unchanged
  React      ──→  FastAPI ──→  src/  ──→  SQLite
  MCP server ──→  FastAPI ──→  src/  ──→  SQLite  (or direct)
```

FastAPI is a thin HTTP wrapper around the same `src/` functions.
yfinance price fetching stays in Python — no reimplementation in Node.js needed.

### Migration path (three phases)

```
Phase A (now):     SQLite + views + src/ + Streamlit
Phase B (future):  Add FastAPI — Streamlit still works, React can be built in parallel
Phase C (future):  React frontend promoted, Streamlit retired
```

### Rules to keep migration clean

These are enforced today so the eventual migration is low-friction:

1. **No business logic in `dashboard/app.py`** — all data loading and computation
   lives in `src/` modules. The dashboard only formats and renders.
2. **No raw SQL in the dashboard** — use `db.py` functions or views.
3. **No Streamlit state as a data store** — computed values that need to persist
   belong in the DB, not `st.session_state`.
4. **`src/` functions return plain Python types** (dicts, DataFrames) — no
   transport-layer assumptions, callable from Streamlit, FastAPI, or MCP alike.

### If SQLite outgrows local use

A move to Postgres would require only:
- Update `DB_PATH` / connection string in `db.py`
- Replace `sqlite3` with `psycopg2` / `asyncpg`
- Minor SQL dialect adjustments (`strftime` → `date_part`, etc.)

All business logic, parsers, views, and frontend code are unaffected.

---

## Future Phases (not in scope now)

- **Retirement accounts** — register with `account_group = 'retirement'`; add sidebar toggle or dedicated tab; no schema changes needed
- **FastAPI layer** — expose `src/` as REST endpoints; prerequisite for React frontend
- **React frontend** — replace Streamlit; consumes FastAPI
- **Realized P&L** — buy/sell pair matching from transaction history
- **Net worth history chart** — line chart of `portfolio_snapshots` totals over time
- **Benchmark comparison** — fetch `^GSPC` / `^DJI` at snapshot time, store in separate table
- **Postgres migration** — if multi-user or cloud deployment is needed
