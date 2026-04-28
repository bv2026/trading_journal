# Usage Guide

## Prerequisites

- Python 3.11+
- Dependencies installed: `pip install -r requirements.txt`
  (includes `mcp>=1.0.0`, `yfinance>=0.2.0`, `streamlit`, `plotly`, `pandas`)

---

## 1. Prepare activity files

Place broker CSV exports in the `activity/` folder. The folder is gitignored — files never leave your machine.

### Transaction CSVs (ingested into `data/journal.db`)

| File | Broker / Account | Parser |
|------|-----------------|--------|
| `robinhood-inv-bv.csv` | Robinhood — BV account | `robinhood.py` |
| `robinhood-inv-kd.csv` | Robinhood — KD account | `robinhood.py` |
| `WEBULL-inv.csv` | Webull investment | `webull.py` |
| `WEBULL-cash.csv` | Webull cash | `webull.py` |
| `tdstation-cash.csv` | TradeStation cash activity | `tradestation.py` |
| `schwab.csv` | Schwab | `schwab.py` |
| `tradier.csv` | Tradier | `tradier.py` |
| `coinbase-main.csv` | Coinbase | `coinbase.py` |
| `fidelity_Investment_income_balance.csv` | Fidelity yearly summary | `fidelity.py` |

Missing files are skipped with a warning — you do not need all files present.

### Equity positions CSVs (prices fetched from yfinance at dashboard load)

Export each broker's equity positions and place it in `activity/`:

| File | Account |
|------|---------|
| `positions-scwb.csv` | SCHWAB |
| `positions-trader.csv` | TRADIER |
| `positions-tradestn.csv` | TS (TradeStation) |
| `positions-rh-bv.csv` | RH-BV |
| `positions-rh-kd.csv` | RH-KD |
| `positions-webull.csv` | WEBULL |
| `positions-fidelity.csv` | FIDELITY |
| `positions-coinbase.csv` | COINBASE |

**Required columns:**
```
Ticker, Name, Sh/Contr, COST BASIS, sector, industry, TYPE, IV RANK, PERF YTD, ATR %
```

Columns ignored (computed at runtime from live prices): `PRICE`, `COST`, `MARKET VALUE`, `totalReturn`

**MARGIN row**: include a row with `Ticker = MARGIN` and the current margin balance in `MARKET VALUE`
(e.g. `$(25,000.00)`). The dashboard uses this for Net Worth = Market Value − Margin.

Value formatting accepted: `$1,234.56`, `$(1,234.56)` for negatives, `40%` for percentages, `N/A` for missing.

### Options, futures, and crypto positions — MCP path (preferred)

Options, futures, and crypto positions are written directly from broker API responses
via `mcp_ingest.py`. No CSV files are needed for these asset classes.

Ask Claude to refresh positions after markets close:
> *"Refresh my Tradier positions and options"*
> *"Update Schwab equity, options, and futures"*
> *"Refresh all positions"*

Claude calls the `refresh_positions` MCP tool which fetches live data from each
broker's API and writes it to the DB. Sector/industry enrichment via yfinance runs
automatically at the end of each write.

---

## 2. Account reference

| account_id | broker | asset classes | price_source |
|---|---|---|---|
| `RH-BV` | robinhood | equity | live (yfinance) |
| `RH-KD` | robinhood | equity | live (yfinance) |
| `WEBULL` | webull | equity, options | live (yfinance) |
| `WEBULL-CASH` | webull | equity | live (yfinance) |
| `WEBULL-EVENTS` | webull | event contracts | live (yfinance) |
| `WEBULL-FUT` | webull | futures | static (stored in DB) |
| `TS` | tradestation | equity, options, futures | live (yfinance) |
| `SCHWAB` | schwab | equity, options, futures | live (yfinance) |
| `TRADIER` | tradier | equity, options | live (yfinance) |
| `FIDELITY` | fidelity | equity | live (yfinance) |
| `COINBASE` | coinbase | crypto | static (stored in DB) |

`price_source = live` → equity market value computed from yfinance at dashboard load.
`price_source = static` → market value stored in DB at MCP write time.

---

## 3. Run ingest

```bash
python ingest.py
```

What it does each run:
1. Initialises / migrates `data/journal.db` (additive — no data loss)
2. Registers all accounts
3. **Transactions** — incremental; only new records added (deduplicates by content hash)
4. **Equity positions** — full replace per account; reflects latest CSV export
5. **Sector enrichment** — fills NULL sector/industry in the instruments table via yfinance, propagates to positions
6. **Portfolio snapshot** — records today's market value per account (same-day re-runs update in place)

```bash
# Full rebuild — clears all transactions and reloads from scratch
python ingest.py --reset
```

Example output:
```
Initializing database …
  OK    RH-BV:       0 new  (1785 already in DB)
  OK    SCHWAB:       5 new  (480 already in DB)
  …
Done — 5 new records added, 15502 already existed.
  OK    positions SCHWAB: 18 rows
  OK    positions TRADIER: 12 rows
Positions — 187 rows written across accounts.

Enriching instrument sectors via yfinance …
  Enriched 3 instrument(s) with sector/industry data.

Writing portfolio snapshot …
  Snapshot — 9 accounts written for 2026-04-28
```

---

## 4. Launch the dashboard

```bash
streamlit run dashboard/app.py
# — or double-click launch-dashboard.vbs on Windows
```

Opens at `http://localhost:8501`. Click **Refresh** in the sidebar after re-ingesting.

---

## 5. Dashboard layout

### Page header (always visible)

| Element | Description |
|---------|-------------|
| **Net Worth** | Total market value − margin borrowed |
| **Market Value** | Sum of all equity positions (live prices) |
| **Margin Borrowed** | Total margin outstanding across all accounts |
| **Summary table** | Net Cash Flow · Dividends · Rewards · Div+Rewards · Margin Interest · Fees · Net Income — lifetime totals |

### Portfolio tab *(tab 1)*

| Section | Description |
|---------|-------------|
| **Account Summary** | One row per account: Market Value, Cost, P&L, Return %, Margin, Net Cash, Dividends, Rewards, Margin Int, Fees, Net Income |
| **Sector Allocation** | Market value by sector (pie chart) |
| **Positions by Account** | Collapsible per-account grids; options sub-table shown inside each expander |
| **Sector Summary** | Market value, cost, P&L, allocation %, return %, dividends by sector |
| **Options Summary** | Total MV, count, expiring this week; full options table |
| **Futures Summary** | Total net MV, count; full futures table |

### Yearly Summary tab *(tab 2)*

- Year-over-year table: Deposits, Withdrawals, Net Cash, Dividends, Rewards, Div+Rewards, Margin Interest, Fees, Net Income
- Income breakdown by subcategory per year

### By Account tab *(tab 3)*

- Prev Year / Current Year / ALL pivot tables per account: Net Cash Flow, Div+Rewards, Margin+Fees
- Crypto Flow section (Coinbase external movements)

### Positions tab *(tab 4)*

- **Broker filter** at the top — limits all sub-tabs to selected brokers
- **Equity** — aggregated by symbol: MV, Cost, P&L, Return %, Dividends; metric footer
- **Options** — per-account expanders: symbol, underlying, expiry, strike, call/put, qty, price, MV
- **Futures** — per-account expanders: symbol, qty, price, MV (color-coded)
- **Crypto** — flat table: symbol, qty, price, cost basis, MV; P&L footer

### Transactions tab *(tab 5)*

- Filter by category, broker, year, or description keyword
- Full transaction log sorted by date (most recent first)
- Download filtered results as CSV

### Performance tab *(tab 6)*

- **Portfolio Summary** — Current Value, 1W Ago, $ Change, % Change per account
- **Portfolio Returns** — 1-Week, 1-Month, 3-Month, YTD, 1-Year return % per account
- Both tables show a TOTAL row; periods with no prior snapshot show "—"
- Historical data accumulates with each `python ingest.py` run

---

## 6. Updating data

### Standard transactions (Robinhood, Schwab, Webull, etc.)

1. Download a fresh CSV from your broker
2. Replace the file in `activity/`
3. Run `python ingest.py`
4. Click **Refresh** in the dashboard sidebar

### Fidelity yearly summary

The Fidelity CSV has one row per calendar year, updated throughout the year.

1. Export a fresh "Investment Income" CSV from Fidelity
2. Replace `activity/fidelity_Investment_income_balance.csv`
3. Run `python ingest.py` — Fidelity rows are always fully refreshed

To change the 2020 start year, edit `START_YEAR` in `src/parsers/fidelity.py`.

### Equity positions (CSV path)

1. Export current positions from each broker
2. Replace the corresponding `positions-{account}.csv` in `activity/`
3. Run `python ingest.py` — positions are always fully replaced per account

### Options / futures / crypto positions (MCP path)

Ask Claude to refresh after markets close or whenever you want current data:

```
"Refresh my Tradier positions and options"
"Update Schwab equity, options, and futures — use balance mode for margin"
"Refresh TradeStation positions and balances"
"Refresh all positions"
```

Claude calls `refresh_positions` which fetches live data from each broker's API,
writes it to the DB, and runs sector enrichment automatically.

**Margin modes** (pass when asking Claude to refresh):
- `balance` *(default)* — use margin balance from broker API response
- `computed` — gross MV (sum of positions × price) minus reported equity
- `csv` — preserve the existing MARGIN sentinel already stored in the DB

---

## 7. Adding a new account

### Equity account (CSV path)

1. Write a parser in `src/parsers/<broker>.py`. Each record dict must have:
   ```
   id, account_id, date, category, subcategory,
   amount, currency, symbol, description, source_file
   ```

2. Register in `ingest.py`:
   ```python
   # ACCOUNTS list
   {"account_id": "NEW-ACCT", "broker": "newbroker", "account_type": "equity",
    "account_group": "investment", "holder": None, "price_source": "live", "active": 1},

   # PARSERS list
   (newbroker.parse, ACTIVITY / "newbroker.csv", "NEW-ACCT"),

   # POSITION_FILES list
   (ACTIVITY / "positions-newacct.csv", "NEW-ACCT"),
   ```

3. Run `python ingest.py`.

### Account with options / futures (MCP path)

1. Write a fetcher in `src/fetchers/<broker>.py` with `normalize_positions()`,
   `normalize_history()`, `normalize_instruments()`, and `normalize_balances()`.

2. Add a `write_<broker>()` function in `mcp_ingest.py` following the pattern of
   existing write functions.

3. Register the account in `ingest.py` ACCOUNTS and in `mcp_server.py` `refresh_positions`.

4. Ask Claude to refresh: *"Refresh my new broker positions"*.

---

## 8. MCP Server (Claude Desktop integration)

The MCP server provides read tools for querying the journal and a write tool for
refreshing positions directly from broker APIs.

### Register with Claude Desktop

1. Open (or create) `%APPDATA%\Claude\claude_desktop_config.json`
2. Add under `mcpServers`:

```json
{
  "mcpServers": {
    "trading-journal": {
      "command": "C:\\Users\\vsbra\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
      "args": ["C:\\work\\trading-journal\\mcp_server.py"],
      "cwd": "C:\\work\\trading-journal"
    }
  }
}
```

3. Restart Claude Desktop.

### Available tools

| Tool | Description |
|------|-------------|
| `get_portfolio_summary` | Overall KPIs + live net worth across all asset classes |
| `get_yearly_summary` | Year-over-year breakdown table |
| `get_account_summary` | Per-account breakdown table |
| `get_transactions` | Filterable transaction log (category, account, year, keyword) |
| `get_positions` | Current holdings across equity, options, futures, and crypto |
| `get_performance` | Account-level returns: 1W / 1M / 3M / YTD / 1Y |
| `refresh_positions` | Fetch live positions from broker APIs and write to DB |
| `run_ingest` | Re-run the CSV ingest pipeline |
| `launch_dashboard` | Start the Streamlit dashboard in the background |

### `refresh_positions` parameters

Each broker's data is passed as a JSON string. Omit a broker to skip it.

| Parameter | Broker | What to pass |
|-----------|--------|-------------|
| `tradier_positions` | Tradier | `get_positions` response |
| `tradier_quotes` | Tradier | `get_market_quotes` response (for option prices) |
| `tradier_history` | Tradier | `get_account_history` response |
| `schwab_equity` | Schwab | `get_equity_positions` response |
| `schwab_futures` | Schwab | `get_futures_positions` response |
| `schwab_summary` | Schwab | `get_account_summary` response |
| `schwab_txns` | Schwab | `get_transactions` response |
| `ts_positions` | TradeStation | `get-positions-details` response |
| `ts_balances` | TradeStation | `get-balances-details` response |
| `rh_positions` | Robinhood | `get_positions` response |
| `rh_portfolio` | Robinhood | `get_portfolio` response |
| `webull_accounts` | Webull | `get_account_list` result text |
| `webull_positions` | Webull | JSON map of `{wb_id: positions_text}` |
| `margin_mode` | All | `"balance"` \| `"computed"` \| `"csv"` |

---

## 9. Category reference

| Category | Subcategories | Sign |
|----------|--------------|------|
| `cash_flow` | `deposit`, `withdrawal`, `internal_transfer` | + / − |
| `crypto_flow` | `usd_deposit`, `usd_withdrawal`, `bank_purchase`, `crypto_received`, `crypto_sent` | + / − |
| `dividend` | `cash_div`, `manufactured_div`, `reinvested_div`, `nonqualified_div`, `substitute_income`, `prior_yr_div` | + |
| `reward` | `interest`, `staking`, `reward_income`, `platform_reward`, `securities_lending`, `credit_card_reward`, `learning_reward`, `subscription_rebate`, `incentive` | + |
| `margin_interest` | `monthly`, `aggregated_margin` | − |
| `fee` | `trading_fee`, `subscription_fee`, `platform_fee`, `clearing_fee`, `commission` | − |
| `other` | *(filtered out of dashboard)* | |
