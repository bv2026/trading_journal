# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Ingest broker CSVs (incremental — only new records added)
python ingest.py

# Full rebuild from scratch
python ingest.py --reset

# Launch dashboard
streamlit run dashboard/app.py         # http://localhost:8501

# MCP server (for Claude Desktop)
python mcp_server.py

# Tests
python -m pytest tests/ -q             # all tests
python -m pytest tests/ -x -q         # stop on first failure
python -m pytest tests/unit/ -q        # unit only
python -m pytest tests/integration/ -q # integration only
python -m pytest tests/unit/test_parsers.py::test_robinhood_parse -v  # single test
```

## Architecture

Two parallel ingest paths feed the same SQLite database (`data/journal.db`):

**CSV path** (`ingest.py` → `src/parsers/` → `src/db.py`)
- Reads broker CSV exports from `activity/`
- Transactions: incremental, content-based IDs via `make_id()` prevent duplicates
- Equity positions: always fully replaced per account (`delete_positions_by_account` + `insert_positions`)
- Fidelity is an exception — it's a yearly summary file, so it's always deleted and re-inserted even without `--reset`

**MCP path** (`mcp_ingest.py` → `src/fetchers/` → `src/db.py`)
- Claude calls broker MCP tools in-session, then passes the raw response dicts to `write_*` functions in `mcp_ingest.py`
- `src/fetchers/` normalizes each broker's response format into DB rows; `src/fetchers/base.py` has shared OCC symbol parsing, currency detection, and ID hashing
- All MCP writes call `enrich_sectors()` automatically after writing

**After either path**, `ingest.py` also:
1. Calls `enrich_sectors()` to fill NULL `sector`/`industry` on the `instruments` table via yfinance
2. Writes a `portfolio_snapshots` row for today — this is what powers the Performance tab returns

## Database schema key points

- `positions` — equity only; `price_source='live'` accounts get prices fetched from yfinance at dashboard load; `price_source='static'` accounts use `stored_price`
- `options_positions`, `futures_positions`, `crypto_positions` — always store `market_value` at ingest time (no live fetch needed)
- `instruments` — shared metadata table (sector, industry, expiry, strike, etc.) keyed on `(symbol, asset_class)`
- `portfolio_snapshots` — one row per (date, account); queried by `v_snapshot_periods` view to compute 1W/1M/3M/YTD/1Y returns
- `v_positions_all` view — unified UNION ALL across all 4 position tables for cross-asset queries
- Schema migrations are handled inline in `db._migrate()` — add new `ALTER TABLE` statements there, they're idempotent

## Parser conventions (`src/parsers/`)

- Each parser exposes a `parse(filepath, account_id) -> list[dict]` function
- All amounts go through `parse_amount()` in `utils.py` — handles `$1,234.56`, `$(1,234.56)`, negatives
- All dates go through `parse_date()` — returns `YYYY-MM-DD`; handles Schwab's `"04/09/2026 as of 04/08/2026"` format
- Transaction IDs use `make_id(account_id, date, amount, note)` — MD5 hash, enables deduplication on re-ingest
- Webull is split into two parsers: `parse_inv` (investment account) and `parse_cash` (cash account), both writing to the `WEBULL` account

## Fetcher conventions (`src/fetchers/`)

- Each fetcher normalizes one broker's MCP response into `(positions_rows, options_rows, futures_rows, transactions_rows)`
- Option symbols are normalized to OCC format for cross-broker consistency: `{underlying}{YYMMDD}{C/P}{8-digit strike*1000}`
- TradeStation uses its own symbol format (`"MSFT 260717C425"`); `base.parse_ts_option()` converts to OCC
- `base.is_currency_entry()` disambiguates currency-code tickers (e.g. `USD`) from real equities by checking cost-per-unit ≈ $1.00

## pandas 3.0 gotchas

- `StringDtype` columns: always `fillna("")` before `astype(str)` or `isin([...])` checks — `"NAN"` strings will not match `"N/A"` etc.
- MARGIN rows: coerce numerics → capture `MARGIN MARKET VALUE` into `cost_basis` → then drop runtime columns (order matters)

## Sync Positions (MCP → DB)

**Trigger:** user says "sync positions" — run all brokers below in order, then finalize.  
**Working dir:** `C:\work\trading-journal`  
**Temp files:** `data\tmp\` (create with `mkdir -p data\tmp` if missing)  
**Rule:** a failure in one broker must not stop the others — log and continue.

### 1 — Webull (equity + options + futures)
```
mcp__webull__get_account_list
```
Save raw result text → `data\tmp\wb_account_list.txt`

For **each** Webull account ID found in that list:
```
mcp__webull__get_account_positions   account_id=<wb_id>
```
Save each result text → `data\tmp\wb_pos_<wb_id>.txt`

Build `data\tmp\wb_positions_map.json` = `{"<wb_id>": "<positions_text>", ...}` then:
```
python mcp_ingest.py --broker webull --account-list data\tmp\wb_account_list.txt --positions-map data\tmp\wb_positions_map.json
```

### 2 — Schwab (equity + options + futures)
```
mcp__schwab-smartspreads-file__get_equity_positions   → data\tmp\schwab_equity.json
mcp__schwab-smartspreads-file__get_futures_positions  → data\tmp\schwab_futures.json
mcp__schwab-smartspreads-file__get_account_summary    → data\tmp\schwab_summary.json
```
```
python mcp_ingest.py --broker schwab --equity data\tmp\schwab_equity.json --futures data\tmp\schwab_futures.json --summary data\tmp\schwab_summary.json
```

### 3 — Tradier (equity + options)
```
mcp__15d93091-8d01-49f7-b7ff-0837e8640ff6__get_positions       → data\tmp\tradier_pos.json
mcp__15d93091-8d01-49f7-b7ff-0837e8640ff6__get_market_quotes   (all symbols from positions) → data\tmp\tradier_quotes.json
```
```
python mcp_ingest.py --broker tradier --positions data\tmp\tradier_pos.json --quotes data\tmp\tradier_quotes.json
```

### 4 — TradeStation (equity + options)
```
mcp__2350ff9e-36f7-4e64-8285-92896085c7d0__get-positions-details  → data\tmp\ts_pos.json
mcp__2350ff9e-36f7-4e64-8285-92896085c7d0__get-balances-details   → data\tmp\ts_bal.json
```
```
python mcp_ingest.py --broker ts --positions data\tmp\ts_pos.json --balances data\tmp\ts_bal.json
```

### 5 — Robinhood RH-BV only (equity; RH-KD is CSV)
```
mcp__aeae2ef5-2c58-4908-8c9d-937f5b4fbbbf__get_positions  → data\tmp\rh_pos.json
mcp__aeae2ef5-2c58-4908-8c9d-937f5b4fbbbf__get_portfolio  → data\tmp\rh_port.json
```
```
python mcp_ingest.py --broker robinhood --positions data\tmp\rh_pos.json --portfolio data\tmp\rh_port.json
```

### 6 — Finalize
```
python ingest.py --snapshot-only
```
Report rows written per broker and any errors. Hit **Refresh** in the dashboard to see updated positions.

## Compact Instructions

When compacting, always preserve:
- Current task and what has been completed
- File paths and function names being modified
- Errors encountered and how they were resolved
- Test results — what's passing and failing
- Which account/parser/broker is in scope
- Any pending next steps
