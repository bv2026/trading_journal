# Usage Guide

## Prerequisites

- Python 3.11+
- Dependencies installed: `pip install -r requirements.txt` (includes `mcp>=1.0.0`, `yfinance>=0.2.0`)

---

## 1. Prepare activity files

Place all broker CSV exports in the `activity/` folder.
The folder is gitignored — files never leave your machine.

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

### Equity positions CSVs (live prices fetched at runtime via yfinance)

Export each broker's equity positions as a CSV and place it in `activity/`:

| File | Account |
|------|---------|
| `positions-scwb.csv` | SCHWAB |
| `positions-trader.csv` | TRADIER |
| `positions-tradestn.csv` | TS (TradeStation) |
| `positions-rh-bv.csv` | RH-BV |
| `positions-rh-kd.csv` | RH-KD |
| `positions-webull.csv` | WEBULL |
| `positions-fidelity.csv` | FIDELITY |

**Required columns:**

```
Ticker, Name, Sh/Contr, COST BASIS, sector, industry, TYPE, IV RANK, PERF YTD, ATR %
```

Columns that are **ignored** (computed at runtime from live prices):
`PRICE`, `COST`, `MARKET VALUE`, `totalReturn`

**MARGIN row**: include a row with `Ticker = MARGIN` and the current margin balance in `MARKET VALUE`
(e.g. `$(25,000.00)`). The dashboard uses this for Net Worth = Market Value − Margin.

Value formatting accepted: `$1,234.56`, `$(1,234.56)` for negatives, `40%` for percentages, `N/A` for missing.

### Options / futures / crypto positions CSVs (prices stored at ingest time)

These use the **Tradier-style export format** — a single CSV per account type with the
following columns:

```
Symbol, Expiry, Strike, Call/Put, Description, Qty, Price, Market Value, Underlying Symbol
```

Optional / ignored: `Account Type`, `Day Change`

Row routing is automatic based on cell content:

| Row type | Criteria | Target account suffix |
|----------|----------|-----------------------|
| Option | non-empty `Expiry` AND non-empty `Call/Put` | `-OPT` |
| Future | non-empty `Underlying Symbol`, empty `Call/Put` | `-FUT` |
| Crypto | all of `Expiry`, `Strike`, `Call/Put`, `Underlying Symbol` empty | *(crypto account)* |

File naming and account mapping (add entries to `OPTIONS_FILES` / `FUTURES_FILES` /
`CRYPTO_FILES` in `ingest.py` as CSVs become available):

| File | Account |
|------|---------|
| `options-trader.csv` | TRADIER-OPT |
| `options-schwab.csv` | SCHWAB-OPT |
| *(future)* `futures-ts.csv` | TS-FUT |
| *(future)* `crypto-coinbase.csv` | COINBASE |

`Qty` is signed: negative = short position.
`Market Value` is stored as-is — no multiplier is applied (the broker has already
computed `Qty × Price × 100` for options).

---

## 2. Account naming convention

Account IDs follow the pattern `{BROKER}-{HOLDER}[-{TYPE}]`:

- **Suffix-free** accounts (e.g. `RH-BV`, `SCHWAB`) are equity — backward compatible.
- **Type suffix** is appended only for non-equity account types.

| account_id | broker | account_type | price_source |
|---|---|---|---|
| `RH-BV` | robinhood | equity | live |
| `RH-KD` | robinhood | equity | live |
| `WEBULL` | webull | equity | live |
| `TS` | tradestation | equity | live |
| `SCHWAB` | schwab | equity | live |
| `TRADIER` | tradier | equity | live |
| `FIDELITY` | fidelity | equity | live |
| `COINBASE` | coinbase | crypto | static |
| `TRADIER-OPT` | tradier | options | static |
| `SCHWAB-OPT` | schwab | options | static |

`price_source = live` → market value computed at runtime from yfinance.
`price_source = static` → market value stored in DB at ingest time from the CSV.

---

## 3. Run ingest

```bash
python ingest.py
```

What it does on each run:
1. Initialises / migrates `data/journal.db` if needed
2. Registers all accounts in the DB
3. **Transactions** — incremental; only new records added
4. **Equity positions** — full replace per account; reflects latest CSV export
5. **Options / futures / crypto positions** — full replace per account; skips missing files
6. **Portfolio snapshot** — records today's market value per account (live equity prices
   fetched from yfinance; static asset values read from DB); same-day re-runs update
   the row in place rather than duplicating it

```bash
# Full rebuild (clears all transactions and reloads from scratch)
python ingest.py --reset
```

Example output:
```
Initializing database ...
  OK    RH-BV:     0 new  (1785 already in DB)
  OK    SCHWAB:     5 new  (480 already in DB)
  ...
Done — 5 new records added, 15502 already existed.
  OK    positions SCHWAB: 18 rows
  OK    positions TRADIER: 12 rows
  OK    options  TRADIER-OPT: 4 rows
  ...
Positions — 187 rows written across accounts.

Writing portfolio snapshot ...
  Snapshot — 9 accounts written for 2026-04-25
```

---

## 4. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`.
Use the **Refresh** button in the sidebar after re-ingesting to reload data.

---

## 5. Dashboard layout

### Page header (always visible)

| Element | Description |
|---------|-------------|
| **Net Worth** | Market Value − Margin Borrowed |
| **Market Value** | Total current market value (live prices × shares) |
| **Margin Borrowed** | Total margin outstanding across all accounts |
| **Summary table** | Net Cash Flow · Dividends · Rewards · Div+Rewards · Margin Interest · Fees · Net Income — lifetime totals |

### Portfolio tab *(tab 1)*

| Section | Description |
|---------|-------------|
| **Account Summary** | One row per account combining position data (Market Value, Cost, P&L, Return %, Margin) and transaction data (Net Cash, Dividends, Rewards, Margin Interest, Fees, Net Income) |
| **Sector & Account allocation pies** | Market value breakdown by sector and by account |
| **Positions by Account** | Collapsible per-account grids showing all holdings sorted by market value |
| **Sector Summary** | Market value, cost, P&L, allocation %, return %, dividends by GICS sector |

### Yearly Summary tab *(tab 2)*

- Year-over-year table (Deposits, Withdrawals, Net Cash, Dividends, Rewards, Div+Rewards, Margin Interest, Fees, Net Income)
- Income vs Costs stacked bar, Net Income bar, Cash Flow bar, Dividends by account bar
- Drilldown: income by subcategory per year

### By Account tab *(tab 3)*

- Three-column pivot tables per account: Previous Year / Current Year / ALL (lifetime)
- Net Cash Flow, Div+Rewards, and Margin+Fees tables with bold TOTAL footer

### Positions tab *(tab 4)*

- All holdings grouped by symbol with Market Value, Total Cost, P&L, Sector, Return %, Dividends
- Pinned metric footer: total Market Value, Cost, P&L, Return %, Dividends

### Transactions tab *(tab 5)*

- Full filterable / searchable transaction log
- Filter by category, account, year, or description keyword
- Download filtered results as CSV

### Performance tab *(tab 6)*

- **Portfolio Summary** — Current Value, Margin, 1W Ago, $ Change, % Change per account
- **Portfolio Returns** — 1-Week, 1-Month, 3-Month, YTD, 1-Year return % per account
- Both tables show a TOTAL row; periods with no prior snapshot show "—"
- Data accumulates with each `python ingest.py` run

---

## 6. Updating data

### Standard broker accounts (Robinhood, Schwab, Webull, etc.)

1. Download a fresh CSV from your broker
2. Replace the file in `activity/`
3. Run `python ingest.py`
4. Click **Refresh** in the dashboard sidebar

### Fidelity yearly summary

The Fidelity CSV contains one row per calendar year updated throughout the year.

1. Export a fresh "Investment Income" CSV from Fidelity
2. Replace `activity/fidelity_Investment_income_balance.csv`
3. Run `python ingest.py`

Only years from **2020 onwards** are ingested. To change this cutoff, edit
`START_YEAR` at the top of `src/parsers/fidelity.py`.

### Equity positions

1. Export current positions from each broker
2. Replace the corresponding `positions-{account}.csv` in `activity/`
3. Run `python ingest.py` — positions are always fully replaced per account

### Options / futures / crypto positions

1. Export current positions from your broker in Tradier-style CSV format
2. Place the file in `activity/` using the filename registered in `ingest.py`
3. Run `python ingest.py`

Live equity prices are cached for 5 minutes. Static asset prices are stored in the DB
at ingest time and do not require yfinance.

---

## 7. Adding a new account

### Equity account

1. Write a parser in `src/parsers/<broker>.py` following the pattern of existing parsers.
   Each record dict must have:
   ```
   id, account_id, date, category, subcategory,
   amount, currency, symbol, description, source_file
   ```

2. Register it in `ingest.py`:
   ```python
   # In ACCOUNTS list
   {"account_id": "NEW-ACCT", "broker": "newbroker", "account_type": "equity",
    "account_group": "investment", "holder": None, "price_source": "live", "active": 1},

   # In PARSERS list
   (newbroker.parse, ACTIVITY / "newbroker.csv", "NEW-ACCT"),

   # In POSITION_FILES list
   (ACTIVITY / "positions-newacct.csv", "NEW-ACCT"),
   ```

3. Run `python ingest.py`.

### Options / futures / crypto account

1. Export positions in Tradier-style CSV format (see Section 1)
2. Register the account and file in `ingest.py`:
   ```python
   # In ACCOUNTS list — use the correct account_type and price_source
   {"account_id": "NEW-OPT", "broker": "newbroker", "account_type": "options",
    "account_group": "investment", "holder": None, "price_source": "static", "active": 1},

   # In OPTIONS_FILES (or FUTURES_FILES / CRYPTO_FILES)
   (ACTIVITY / "options-new.csv", "NEW-OPT"),
   ```

3. Run `python ingest.py`.

---

## 8. MCP Server (Claude Desktop integration)

The MCP server exposes the portfolio database and live positions as tools
that Claude can call from Claude Desktop.

### Available tools

| Tool | Description |
|------|-------------|
| `get_portfolio_summary` | Overall KPIs (cash flow, dividends, etc.) + live net worth across all asset classes |
| `get_yearly_summary` | Year-over-year breakdown table |
| `get_account_summary` | Per-account breakdown table |
| `get_transactions` | Filterable transaction log (category, account, year, search) |
| `get_positions` | Current holdings across equity, options, futures, and crypto; filter by account, asset_class, sector, or type |
| `get_performance` | Account-level returns across 1W / 1M / 3M / YTD / 1Y lookback periods |
| `run_ingest` | Re-load all broker CSVs into the database |
| `launch_dashboard` | Start the Streamlit dashboard in the background |

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

### Example prompts

**Portfolio overview**
- *"What is my net worth today?"*
- *"Show me my portfolio summary"*
- *"What is my all-time net income across all accounts?"*

**Positions & allocations**
- *"What are my Technology positions in Schwab?"*
- *"Show me all my options positions"*
- *"What is my total market value including options and crypto?"*
- *"Show me unrealized P&L by sector"*

**Performance**
- *"How has my portfolio performed over the last month?"*
- *"Which account has the best YTD return?"*
- *"Show me my 1-week return for each account"*

**Year-over-year**
- *"Show me dividends year by year"*
- *"Which year had the highest net income?"*
- *"How did 2024 compare to 2023 for fees and margin interest?"*

**Per-account drilldown**
- *"How is Fidelity performing vs Robinhood?"*
- *"Which account generates the most dividends?"*
- *"Show me all accounts for 2024"*

**Transactions**
- *"Show me all Coinbase staking rewards"*
- *"List my largest dividends in 2024"*
- *"Find all margin interest charges for RH-BV"*

**Data management**
- *"Ingest the latest files"* → calls `run_ingest`
- *"Launch the dashboard"* → calls `launch_dashboard`

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
