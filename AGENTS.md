# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Commands

```bash
# Ingest broker CSVs (incremental — only new records added)
python -m src.ingest

# Full rebuild from scratch
python -m src.ingest --reset

# Snapshot only (write today's portfolio_snapshots row without re-parsing CSVs)
python -m src.ingest --snapshot-only

# Set cash balance (Fidelity/PNC/Huntington/Clearview combined)
python -m src.cash              # print current balance
python -m src.cash 18500        # set balance

# Launch dashboard
streamlit run dashboard/app.py         # http://localhost:8501

# MCP server (for Codex Desktop)
python -m src.mcp_server

# Tests
python -m pytest tests/ -q             # all tests
python -m pytest tests/ -x -q         # stop on first failure
python -m pytest tests/unit/ -q        # unit only
python -m pytest tests/integration/ -q # integration only
python -m pytest tests/unit/test_parsers.py::test_robinhood_parse -v  # single test
```

## Architecture

Two parallel ingest paths feed the same SQLite database (`data/journal.db`):

**CSV path** (`src/ingest.py` → `src/parsers/` → `src/db.py`)
- Reads broker CSV exports from `activity/`
- Transactions: incremental, content-based IDs via `make_id()` prevent duplicates
- Equity positions: always fully replaced per account (`delete_positions_by_account` + `insert_positions`)
- Fidelity is an exception — it's a yearly summary file, so it's always deleted and re-inserted even without `--reset`

**MCP path** (`src/mcp_ingest.py` → `src/fetchers/` → `src/db.py`)
- Codex calls broker MCP tools in-session, then passes the raw response dicts to `write_*` functions in `src/mcp_ingest.py`
- `src/fetchers/` normalizes each broker's response format into DB rows; `src/fetchers/base.py` has shared OCC symbol parsing, currency detection, and ID hashing
- All MCP writes call `enrich_sectors()` automatically after writing

**After either path**, `src/ingest.py` also:
1. Calls `enrich_sectors()` to fill NULL `sector`/`industry` on the `instruments` table via yfinance
2. Writes a `portfolio_snapshots` row for today — this is what powers the Performance tab returns

`src/ingest.py` also defines three empty list constants for future CSV-based static position imports (currently unused but required by tests that monkey-patch them):
```python
OPTIONS_FILES: list[tuple] = []
FUTURES_FILES: list[tuple] = []
CRYPTO_FILES:  list[tuple] = []
```
Each entry would be `(path, account_id)` parsed via `static_positions_csv.parse(path, acct, asset_type)`.

## Database schema key points

- `positions` — equity only; `price_source='live'` accounts get prices fetched from yfinance at dashboard load; `price_source='static'` accounts use `stored_price`
- `options_positions`, `futures_positions`, `crypto_positions` — always store `market_value` at ingest time (no live fetch needed)
- `cash_accounts` — single combined cash balance (Fidelity/PNC/Huntington/Clearview); upserted via `db.upsert_cash_balance()`
- `instruments` — shared metadata table (sector, industry, expiry, strike, etc.) keyed on `(symbol, asset_class)`
- `portfolio_snapshots` — one row per (date, account); queried by `v_snapshot_periods` view to compute 1W/1M/3M/YTD/1Y returns
- `v_snapshot_periods` — all period columns (`current_value`, `value_1w`, `value_1m`, etc.) are **net of margin** (`market_value - COALESCE(margin, 0)`); ensures Performance tab comparisons are apples-to-apples
- `v_positions_all` view — unified UNION ALL across all 4 position tables for cross-asset queries
- Schema migrations are handled inline in `db._migrate()` — add new `ALTER TABLE` statements there, they're idempotent
- Views are `DROP VIEW IF EXISTS; CREATE VIEW` (not `CREATE VIEW IF NOT EXISTS`) so they always recreate on `init_db()`

## Accounts

| account_id    | broker       | ingest path | asset classes            |
|---------------|--------------|-------------|--------------------------|
| RH-BV         | robinhood    | MCP         | equity, margin           |
| RH-KD         | robinhood    | CSV         | equity                   |
| WEBULL        | webull       | MCP         | equity, margin           |
| WEBULL-CASH   | webull       | MCP         | equity (cash)            |
| WEBULL-EVENTS | webull       | MCP         | event contracts          |
| WEBULL-FUT    | webull       | MCP         | futures                  |
| TS            | tradestation | MCP         | equity, margin           |
| SCHWAB        | schwab       | MCP         | equity, futures, margin  |
| TRADIER       | tradier      | MCP         | equity, options, margin  |
| FIDELITY      | fidelity     | CSV         | equity                   |
| COINBASE      | coinbase     | CSV         | crypto                   |
| CASH          | Multi-Bank   | manual      | cash (combined)          |

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
- `tradestation.normalize_balances()` falls back to first key in accounts map (TS uses numeric account ID as key, not "TS")
- `tradier.normalize_balances()` computes margin as `gross_equity_mv - totalEquity` (Tradier API does not expose marginBalance directly)

## Margin sentinel rows

Each MCP-synced margin account gets a `MARGIN` row inserted into `positions` with `cost_basis = -margin_amount`. The dashboard reads this to show margin debt and compute net equity. Written by `_insert_margin_sentinel()` in `src/mcp_ingest.py`.

Margin accuracy by broker:
- **RH-BV**: exact — portfolio API returns cash balance
- **TS**: exact — balances API returns `currentCashBalance`
- **WEBULL**: exact — balance API returns `Total Cash Balance`
- **SCHWAB**: exact — account summary returns `margin_balance`
- **TRADIER**: approximate — computed as `gross_equity_mv - totalEquity` (off by ~$5–6K due to options impact)

## Dashboard — Portfolio tab layout

The Portfolio tab (first tab in `dashboard/app.py`) renders in this order:
1. **Net Worth banner** — total net worth KPI row (equity + options + futures + crypto + cash − margin)
2. **Account Summary** — 6 columns: Account, Broker, Market Value, Cost Basis, Margin, Net Equity; one row per account + CASH row at bottom; CASH row has Market Value = Cost Basis (zero P/L)
3. **Asset Class Breakdown** — Stocks / Options / Futures / Crypto / Cash rows + TOTAL, with Allocation %
4. **Sector Breakdown** (pie chart + table) — sectors collapsed as below
5. **Futures by Commodity** — futures grouped by root symbol (e.g. `/GCZ26` → `/GC`); shows Contracts and Net MV

Sector collapse rules (applied in display layer, not stored in DB):
- `SECTOR_OVERRIDES` in `src/positions.py` maps specific tickers: BND→"Fixed Income", VTI→"Broad Market", VEA/VWO→"International"
- Dashboard collapses `{"Fixed Income", "Broad Market", "International"}` → `"ETF"`
- Any row with `TYPE == "ETF"` also maps to `"ETF"` (except `"Income ETF"` which is preserved)
- `"Unknown"` → `"Other"`

Futures root extraction: regex `r'(/[A-Z]+)(?=[A-Z]\d{2})'` strips contract-month letter + 2-digit year suffix (e.g. `/GCZ26` → `/GC`, `/VXMH27` → `/VXM`).

## pandas 3.0 gotchas

- `StringDtype` columns: always `fillna("")` before `astype(str)` or `isin([...])` checks — `"NAN"` strings will not match `"N/A"` etc.
- MARGIN rows: coerce numerics → capture `MARGIN MARKET VALUE` into `cost_basis` → then drop runtime columns (order matters)
- `style.format()` — always pass `na_rep=""` to prevent `TypeError: unsupported format string passed to NoneType` when columns contain None (e.g. options strike/price)

## Sync Positions (MCP → DB)

**Trigger:** user says "sync positions" — run all brokers below in order, then finalize.
**Working dir:** `C:\work\trading-journal`
**Temp files:** `data\tmp\` (create with `mkdir -p data\tmp` if missing)
**Rule:** a failure in one broker must not stop the others — log and continue.

### 1 — Webull (equity + options + futures + balance)
```
mcp__webull__get_account_list
```
Save raw result text → `data\tmp\wb_account_list.txt`

For **each** Webull account ID found in that list:
```
mcp__webull__get_account_positions   account_id=<wb_id>
mcp__webull__get_account_balance     account_id=<wb_id>   (INDIVIDUAL_MARGIN account only)
```
Save positions text → `data\tmp\wb_pos_<wb_id>.txt`

Build two maps:
- `data\tmp\wb_positions_map.json` = `{"<wb_id>": "<positions_text>", ...}`
- `data\tmp\wb_balances_map.json`  = `{"<margin_wb_id>": "<balance_text>"}` (INDIVIDUAL_MARGIN wb_id only)

```
python -m src.mcp_ingest --broker webull --account-list data\tmp\wb_account_list.txt --positions-map data\tmp\wb_positions_map.json --balances-map data\tmp\wb_balances_map.json
```

Webull INDIVIDUAL_MARGIN account ID: `8AGMH0413MK07EPRI7J4OOSVH9`

### 2 — Schwab (equity + futures + balance)
```
mcp__schwab-smartspreads-file__get_equity_positions   → data\tmp\schwab_equity.json
mcp__schwab-smartspreads-file__get_futures_positions  → data\tmp\schwab_futures.json
mcp__schwab-smartspreads-file__get_account_summary    → data\tmp\schwab_summary.json
```
```
python -m src.mcp_ingest --broker schwab --equity data\tmp\schwab_equity.json --futures data\tmp\schwab_futures.json --summary data\tmp\schwab_summary.json
```

### 3 — Tradier (equity + options + balance)
```
mcp__15d93091-8d01-49f7-b7ff-0837e8640ff6__get_positions         → data\tmp\tradier_pos.json
mcp__15d93091-8d01-49f7-b7ff-0837e8640ff6__get_market_quotes     (all symbols from positions) → data\tmp\tradier_quotes.json
mcp__15d93091-8d01-49f7-b7ff-0837e8640ff6__get_account_balances  accountNumber=6YB44166 → data\tmp\tradier_balances.json
```
```
python -m src.mcp_ingest --broker tradier --positions data\tmp\tradier_pos.json --quotes data\tmp\tradier_quotes.json --balances data\tmp\tradier_balances.json
```
Note: Tradier API does not expose `marginBalance` — margin is approximated as gross equity MV minus `totalEquity`.

### 4 — TradeStation (equity + balance)
```
mcp__2350ff9e-36f7-4e64-8285-92896085c7d0__get-positions-details  accounts=11908624 → data\tmp\ts_positions.json
mcp__2350ff9e-36f7-4e64-8285-92896085c7d0__get-balances-details   accounts=11908624 → data\tmp\ts_balances.json
```
```
python -m src.mcp_ingest --broker ts --positions data\tmp\ts_positions.json --balances data\tmp\ts_balances.json
```

### 5 — Robinhood RH-BV only (equity; RH-KD is CSV)

Robinhood requires periodic re-linking via `link_robinhood` (sessions expire). If `check_login_status` returns `robinhood_linked: false`, call `link_robinhood` with credentials — it sends a phone push notification to approve, then call `complete_robinhood_link`.
```
mcp__aeae2ef5-2c58-4908-8c9d-937f5b4fbbbf__get_positions  account_number=869439976 → data\tmp\rh_pos.json
mcp__aeae2ef5-2c58-4908-8c9d-937f5b4fbbbf__get_portfolio  account_number=869439976 → data\tmp\rh_port.json
```
```
python -m src.mcp_ingest --broker robinhood --positions data\tmp\rh_pos.json --portfolio data\tmp\rh_port.json
```

### 6 — Finalize
```
python -m src.ingest --snapshot-only
```
Report rows written per broker and any errors. Hit **Refresh** in the dashboard to see updated positions.

### Coinbase — spot balances

Coinbase spot balances are fetched through `coinbase-derivatives-mcp`, not by
passing a hand-built JSON file. The repeatable journal-side command is:
```bash
python scripts/sync_coinbase.py
```
It reads `coinbase-derivatives-mcp` settings from Claude Desktop config, calls
`capture_coinbase_portfolio_snapshot`, imports normalized `query_current_balances`
rows into `crypto_positions`, and writes today's portfolio snapshot. Use
`python scripts/sync_coinbase.py --dry-run` to verify counts without writing `journal.db`.

## Cash balance (manual)

Fidelity CMA + PNC + Huntington + Clearview are combined into a single `CASH` account. Update whenever balances change:
```bash
python -m src.cash 18500        # set combined balance
python -m src.cash              # check current
python -m src.ingest --cash 18500  # alternative via ingest flag
```

## CLI health preflight

`python -m src.journal_cli` runs an MCP health preflight before showing account
balances. It reads broker MCP server definitions from Claude Desktop config and
Codex config, initializes configured stdio servers, checks known remote HTTP MCP
URLs, and lists tools. Remote URL overrides: `ROBINHOOD_MCP_URL`,
`TRADESTATION_MCP_URL`, `TRADIER_MCP_URL`; optional bearer env vars:
`ROBINHOOD_MCP_BEARER_TOKEN`, `TRADESTATION_MCP_BEARER_TOKEN`,
`TRADIER_MCP_BEARER_TOKEN`. This confirms MCP reachability only; balances are
still read from `data/journal.db` and reflect the last completed sync. Menu
option `5. MCP health` reruns the check.

For remote OAuth MCP authorization, use `python scripts/authorize_mcp.py <broker>
--manual`, where broker is `robinhood`, `tradestation`, or `tradier`. The user
opens the printed URL and pastes the final callback URL/code back into the
terminal. Saved tokens are in `data/mcp_tokens/`; use `--reset` to clear a
partial/expired authorization for that broker.

Robinhood via Trayd supports multiple local profiles in `~/.trayd/*.json`.
`src.cli.robinhood` enumerates profiles, calls `list_accounts`, and then calls
account-number scoped `get_portfolio` / `get_positions`. Map Robinhood account
numbers to journal account IDs in ignored `data/config/robinhood_accounts.json`
or with `ROBINHOOD_ACCOUNT_MAP` JSON.

Robinhood linking is separate from Trayd OAuth. Use
`python -m src.cli.robinhood --login --profile <name>` to create/refresh the
local Trayd OAuth token, then
`python -m src.cli.robinhood --link-robinhood --profile <name>` to prompt for
Robinhood email/password, trigger email/phone approval, complete the Trayd
`link_robinhood` flow, and avoid storing the password.

## Test coverage

426 tests passing across 10 files:

| File | Tests | Scope |
|------|-------|-------|
| `tests/unit/test_parsers.py` | ~80 | CSV parsers (robinhood, coinbase, schwab, tradier, ts) |
| `tests/unit/test_parsers_missing.py` | 62 | parsers with previously missing coverage (schwab, tradier, ts, webull, fidelity) |
| `tests/unit/test_fetcher_base.py` | 38 | `src/fetchers/base.py` — OCC parsing, currency detection, ID hashing |
| `tests/unit/test_fetcher_schwab.py` | 37 | `src/fetchers/schwab.py` — all normalize_* functions |
| `tests/unit/test_portfolio_tab.py` | 29 | Dashboard portfolio logic — sector collapse, futures root, account summary, net worth |
| `tests/unit/test_positions.py` | ~30 | `src/positions.py` — load_positions_from_db, sector overrides |
| `tests/integration/test_db_helpers.py` | 19 | `src/db.py` — clear_transactions, instruments, cash_balance, _migrate |
| `tests/integration/test_snapshot_performance.py` | 13 | `v_snapshot_periods` — net-of-margin returns, multi-account, NULL history |
| `tests/integration/test_positions_db.py` | ~60 | positions table CRUD |
| `tests/integration/test_static_positions_db.py` | ~58 | options/futures/crypto tables CRUD |

Run all: `python -m pytest tests/ -q`

## Compact Instructions

When compacting, always preserve:
- Current task and what has been completed
- File paths and function names being modified
- Errors encountered and how they were resolved
- Test results — what's passing and failing
- Which account/parser/broker is in scope
- Any pending next steps
