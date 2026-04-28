# Trading Journal — Architecture & Design

## Overview

Personal portfolio tracker consolidating brokerage activity and live positions
across 11 accounts into a single SQLite database, with a Streamlit dashboard
and Claude Desktop MCP integration.

---

## Architecture

```
activity/                   Broker CSV exports (gitignored)
  archive/                  Retired files kept for reference
data/journal.db             SQLite — all tables
src/
  db.py                     DB helpers: init, migrate, upsert, insert, load
  metrics.py                compute_metrics, colour_cell, style_table
  positions.py              load_positions_from_db, load_all_positions, yfinance price fetch
  enrichment.py             enrich_sectors() — fills NULL sector/industry via yfinance
  parsers/                  CSV parsers — transactions + equity positions
    positions_csv.py        per-account equity positions CSV
    robinhood.py
    webull.py
    tradestation.py
    schwab.py
    tradier.py
    coinbase.py
    fidelity.py             yearly summary (2020+)
  fetchers/                 MCP response normalizers — broker API → DB records
    base.py                 OCC parsing, TS option parsing, currency detection, ID hashing
    tradier.py              normalize_positions, normalize_history, normalize_instruments, normalize_balances
    tradestation.py         normalize_positions (3-tuple), normalize_instruments, normalize_balances
    webull.py               account_map_from_list, parse_positions_text, normalize_positions (4-tuple)
    robinhood.py            normalize_positions, normalize_portfolio, normalize_instruments
    schwab.py               normalize_equity, normalize_futures, normalize_transactions,
                            normalize_instruments, normalize_balances
dashboard/
  app.py                    Streamlit — 6 tabs
mcp_server.py               FastMCP server (Claude Desktop)
mcp_ingest.py               write_* functions — normalize MCP responses → DB
ingest.py                   CSV ingest pipeline → journal.db; portfolio snapshot
schema.sql                  All table + view definitions
tests/
  unit/                     Parser-level unit tests
  integration/              DB round-trip integration tests
```

---

## DB Schema

### `accounts`

```sql
accounts (
    account_id     TEXT PRIMARY KEY,
    broker         TEXT NOT NULL,
    account_type   TEXT DEFAULT 'equity',   -- equity | futures | crypto
    account_group  TEXT DEFAULT 'investment',
    holder         TEXT,
    price_source   TEXT DEFAULT 'live',     -- live (yfinance) | static (stored in DB)
    active         INTEGER DEFAULT 1
)
```

### `transactions`

```sql
transactions (
    id           TEXT PRIMARY KEY,          -- content-based MD5 hash
    account_id   TEXT NOT NULL REFERENCES accounts,
    date         DATE NOT NULL,
    category     TEXT NOT NULL,             -- cash_flow | dividend | margin_interest | fee | reward | other
    subcategory  TEXT,
    amount       REAL NOT NULL,             -- positive = inflow, negative = outflow (USD)
    currency     TEXT DEFAULT 'USD',
    symbol       TEXT,
    description  TEXT,
    data_source  TEXT,                      -- mcp | csv
    source_file  TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### `positions` (equity)

```sql
positions (
    account_id    TEXT NOT NULL REFERENCES accounts,
    ticker        TEXT NOT NULL,
    name          TEXT,
    shares        REAL,
    cost_basis    REAL,                     -- per-share cost
    stored_price  REAL,                     -- NULL for live accounts; set by MCP writes
    sector        TEXT,
    industry      TEXT,
    asset_type    TEXT,                     -- Stock | ETF | margin
    iv_rank       REAL,
    perf_ytd      REAL,
    atr_pct       REAL,
    data_source   TEXT,                     -- mcp | csv
    source_file   TEXT,
    ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, ticker)
)
```

MARGIN sentinel: rows with `ticker = 'MARGIN'` and `cost_basis = -margin_amount`.
The dashboard reads `cost_basis` for these rows and treats it as a negative market value.

### `options_positions`

```sql
options_positions (
    account_id   TEXT NOT NULL REFERENCES accounts,
    symbol       TEXT NOT NULL,             -- OCC format: GOOGL270115C00360000
    underlying   TEXT,
    expiry       TEXT,                      -- YYYY-MM-DD
    strike       REAL,
    call_put     TEXT,                      -- C | P
    description  TEXT,
    qty          REAL,                      -- negative = short
    price        REAL,                      -- per-share mark price
    market_value REAL,                      -- qty × price × 100
    data_source  TEXT,                      -- mcp | csv
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
)
```

### `futures_positions`

```sql
futures_positions (
    account_id   TEXT NOT NULL REFERENCES accounts,
    symbol       TEXT NOT NULL,             -- /ESM25, /CLM25 etc.
    underlying   TEXT,
    description  TEXT,
    qty          REAL,
    price        REAL,
    market_value REAL,
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
)
```

### `crypto_positions`

```sql
crypto_positions (
    account_id   TEXT NOT NULL REFERENCES accounts,
    symbol       TEXT NOT NULL,             -- BTC, ETH etc.
    name         TEXT,
    qty          REAL,
    price        REAL,
    cost_basis   REAL,
    market_value REAL,
    source_file  TEXT,
    ingested_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, symbol)
)
```

### `instruments` (master table)

```sql
instruments (
    symbol        TEXT NOT NULL,
    asset_class   TEXT NOT NULL,            -- equity | option | future | crypto
    underlying    TEXT,
    name          TEXT,
    exchange      TEXT,
    currency      TEXT DEFAULT 'USD',
    sector        TEXT,
    industry      TEXT,
    expiry        TEXT,
    strike        REAL,
    call_put      TEXT,
    tick_size     REAL,
    point_value   REAL,
    tradable      TEXT,
    fetched_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, asset_class)
)
```

Written by `normalize_instruments()` in each fetcher. Sector/industry filled by `enrich_sectors()` after every MCP write and CSV ingest.

### `portfolio_snapshots`

```sql
portfolio_snapshots (
    snapshot_date  TEXT NOT NULL,           -- YYYY-MM-DD
    account_id     TEXT NOT NULL REFERENCES accounts,
    market_value   REAL NOT NULL,
    cost_basis     REAL,
    margin         REAL DEFAULT 0.0,
    PRIMARY KEY (snapshot_date, account_id)
)
```

Written at the end of every `ingest.py` run via `INSERT OR REPLACE`.

---

## SQL Views

| View | Purpose |
|------|---------|
| `v_positions_all` | Unified across all 4 position tables; equity `market_value` is NULL (requires live price fetch) |
| `v_transaction_summary` | Transaction totals per account per year |
| `v_yearly_summary` | Cross-account yearly rollup |
| `v_snapshot_latest` | Most recent snapshot per account |
| `v_snapshot_periods` | Per-account MV at standard lookback periods (1W / 1M / 3M / 1Y / YTD-start) |

---

## Ingest Pipelines

### CSV ingest (`ingest.py`)

```
python ingest.py [--reset]
  ├── init_db()                 create/migrate all tables and views
  ├── upsert_accounts()         register all accounts
  ├── TRANSACTIONS (incremental)
  │   └── for each PARSERS entry:
  │         parse CSV → insert_transactions()   INSERT OR IGNORE
  ├── EQUITY POSITIONS (full replace per account)
  │   └── for each POSITION_FILES entry:
  │         parse → delete_positions_by_account() → insert_positions()
  ├── SECTOR ENRICHMENT
  │   └── enrich_sectors()      fill NULL sector/industry in instruments → propagate to positions
  └── PORTFOLIO SNAPSHOT
        load all positions (equity via yfinance, static from DB)
        write_portfolio_snapshot(date=today)    INSERT OR REPLACE
```

### MCP-first ingest (`mcp_ingest.py`)

Used when Claude fetches live data directly from broker APIs:

```
write_tradier(positions_resp, quotes_resp, history_resp)
write_schwab(equity_resp, futures_resp, summary_resp, txn_resp)
write_tradestation(positions_resp, balances_resp)
write_robinhood(positions_resp, portfolio_resp)
write_webull(account_list, positions_by_wb_id, balance_by_wb_id)
  Each function:
  ├── normalize_*() via src/fetchers/<broker>.py
  ├── delete_positions_by_account() + insert_positions()
  ├── delete_options_by_account()  + insert_options()
  ├── delete_futures_by_account()  + insert_futures()   (where applicable)
  ├── delete_crypto_by_account()   + insert_crypto()    (where applicable)
  ├── insert_transactions()        (incremental)
  ├── upsert_instruments()
  ├── _insert_margin_sentinel()    (equity accounts with margin)
  └── _enrich()                    → enrich_sectors()
```

---

## Margin Accounting

Margin debt is stored as a MARGIN sentinel row in the `positions` table:
- `ticker = 'MARGIN'`, `asset_type = 'margin'`
- `cost_basis = -margin_amount` (negative)
- `market_value` is derived from `cost_basis` at dashboard load time

Three modes in `write_*` functions (`margin_mode` parameter):
- `"balance"` — use reported margin/cash from the broker's balance API response
- `"computed"` — `gross_MV (Σ shares×price) − reported_equity`
- `"csv"` — preserve the existing sentinel already in the DB

The dashboard `load_positions_from_db()` identifies MARGIN rows and routes them
to a separate `margin_df`; the main `pos` DataFrame excludes them.

---

## Pricing Model

### Equity (`price_source = 'live'`)

At dashboard load time:
```
_fetch_live_prices(tickers) → {ticker: price}   (yfinance, cached 5 min)
MARKET VALUE = Shares × PRICE
COST         = Shares × Cost_Basis
totalReturn  = MARKET VALUE − COST
```

MARGIN rows bypass yfinance: `MARKET VALUE = cost_basis` (already negative).

### Options / Futures / Crypto (`price_source = 'static'`)

`market_value` is stored in DB at write time (from broker API or CSV).
No yfinance lookup needed. The dashboard reads it directly.

---

## Sector Enrichment (`src/enrichment.py`)

`enrich_sectors(batch_size=50)`:
1. Queries `instruments` for equities with NULL sector or industry
2. Fetches `yfinance.Ticker(sym).info` for each in batches
3. `UPDATE instruments SET sector=COALESCE(sector,?), industry=COALESCE(industry,?), name=COALESCE(name,?)`
   — never overwrites existing values
4. `_propagate_to_positions()` — copies back to `positions` rows that still have NULL

Called automatically:
- End of `ingest.py` run
- End of each `write_*` call in `mcp_ingest.py` (wrapped in try/except — non-fatal)

---

## Fetcher Design (`src/fetchers/`)

Each fetcher normalizes a single broker's API responses into DB-ready record dicts.

### `base.py` — shared utilities

- `is_occ_symbol(s)` / `parse_occ(s)` — OCC option format
- `is_ts_option_symbol(s)` / `parse_ts_option(s)` — TradeStation format; converts to OCC for storage
- `is_currency_entry(symbol, total_cost, qty)` — distinguishes cash entries (cost≈$1/unit) from real equities
- `make_txn_id(account_id, date, amount, description)` — content-based MD5 for deduplication
- `parse_iso_date(ts)` — ISO-8601 → `YYYY-MM-DD`

### Return shapes

| Fetcher | `normalize_positions()` returns |
|---------|--------------------------------|
| Tradier | `(equity_records, option_records)` |
| TradeStation | `(equity_records, option_records, futures_records)` |
| Webull | `(equity_records, option_records, futures_records, crypto_records)` |
| Robinhood | `equity_records` (list only; no options via MCP) |
| Schwab | split into `normalize_equity()` → `(eq, opt)` and `normalize_futures()` → `fut` |

---

## Dashboard (`dashboard/app.py`)

### Tabs

| # | Tab | Key data sources |
|---|-----|-----------------|
| 1 | Portfolio | `load_positions_from_db()`, `load_options_from_db()`, `load_futures_from_db()` |
| 2 | Yearly Summary | `load_transactions()` |
| 3 | By Account | `load_transactions()` |
| 4 | Positions | All 4 position loaders; broker filter applied |
| 5 | Transactions | `load_transactions()`; broker + year + keyword filter |
| 6 | Performance | `load_all_positions()`, `load_snapshot_periods()` |

### Caching

All loaders are `@st.cache_data(ttl=300)` — 5 minute TTL.
Transaction loader is `@st.cache_data(ttl=60)`.
Cache is cleared on background ingest completion and on sidebar Refresh button.

---

## MCP Server (`mcp_server.py`)

FastMCP server. Registered tools:

| Tool | Type | Description |
|------|------|-------------|
| `get_portfolio_summary` | read | KPIs + net worth across all asset classes |
| `get_yearly_summary` | read | Year-over-year breakdown |
| `get_account_summary` | read | Per-account breakdown |
| `get_transactions` | read | Filterable transaction log |
| `get_positions` | read | Holdings across equity/options/futures/crypto |
| `get_performance` | read | Return % at 1W / 1M / 3M / YTD / 1Y |
| `refresh_positions` | write | Fetch from broker APIs → normalize → write to DB |
| `run_ingest` | write | Re-run CSV ingest pipeline |
| `launch_dashboard` | action | Start Streamlit in background |

### `refresh_positions` flow

```
refresh_positions(tradier_positions, tradier_quotes, ..., schwab_equity, ...)
  ├── if tradier_*:   write_tradier(...)
  ├── if schwab_*:    write_schwab(...)
  ├── if ts_*:        write_tradestation(...)
  ├── if rh_*:        write_robinhood(...)
  ├── if webull_*:    write_webull(...)
  └── enrich_sectors()   (always runs at end regardless of which brokers provided)
```

Each `write_*` also calls `_enrich()` internally, so enrichment runs incrementally
after each broker regardless of whether others fail.

---

## Account Naming Convention

```
{BROKER}-{HOLDER}[-{TYPE}]
```

Suffix-free accounts (e.g. `RH-BV`, `SCHWAB`) are equity. Type suffix only for
non-equity account types (e.g. `WEBULL-FUT`).

Options and crypto for most brokers live under the parent account ID (e.g. Tradier
options under `TRADIER`, Schwab options under `SCHWAB`). Only Webull has separate
IDs for distinct account types.

---

## DB Migration Strategy

All schema changes are additive. No destructive migrations.

1. `schema.sql` uses `CREATE TABLE IF NOT EXISTS` and `CREATE VIEW IF NOT EXISTS` — idempotent
2. New columns on existing tables are added in `_migrate()` via `ALTER TABLE ... ADD COLUMN` wrapped in try/except — silently skipped if already present

Current `_migrate()` additions:
```python
"ALTER TABLE accounts  ADD COLUMN account_group TEXT DEFAULT 'investment'"
"ALTER TABLE accounts  ADD COLUMN price_source  TEXT DEFAULT 'live'"
"ALTER TABLE accounts  ADD COLUMN active        INTEGER DEFAULT 1"
"ALTER TABLE positions ADD COLUMN stored_price  REAL"
"ALTER TABLE positions ADD COLUMN data_source   TEXT"
"ALTER TABLE options_positions ADD COLUMN data_source TEXT"
```
