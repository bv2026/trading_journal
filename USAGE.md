# Usage Guide

## Prerequisites

- Python 3.11+
- Dependencies installed: `pip install -r requirements.txt` (includes `mcp>=1.0.0`)

---

## 1. Prepare activity files

Place all broker CSV exports **and** the positions workbook in the `activity/` folder.
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

### Positions workbook (read live by the dashboard — never ingested)

| File | Contents |
|------|----------|
| `TRADEPOSITIONS.xlsx` | Current positions for all accounts (one sheet per broker). Prices are live via OptionSamurai / MS STOCK Excel formulas. |

The dashboard re-reads this file on every page load (cached for 5 minutes).
To see updated prices, click **Refresh** in the sidebar.

---

## 2. Run ingest

```bash
python ingest.py
```

- Initialises `data/journal.db` if it does not exist
- **Clears and re-inserts** all transactions on every run (idempotent)
- Prints a per-account record count and flags any errors

Example output:
```
Initializing database ...
Clearing existing transactions ...
Upserting accounts ...
  OK    RH-BV:  1796 records
  OK    RH-KD:   823 records
  OK    WEBULL:  387 records
  OK    FIDELITY: 28 records
  ...
Total: 15545 records (0 duplicates removed)
Ingest complete.
```

---

## 3. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`.
Use the **Refresh** button in the sidebar after re-ingesting to reload transaction data.

---

## 4. Dashboard layout

### Page header (always visible)

| Element | Description |
|---------|-------------|
| **Net Worth** | Market Value − Margin Borrowed (live from `TRADEPOSITIONS.xlsx`) |
| **Market Value** | Total current market value across all position accounts |
| **Margin Borrowed** | Total margin outstanding across all accounts |
| **Summary table** | Net Cash Flow · Dividends · Rewards · Div+Rewards · Margin Interest · Fees · Net Income — filtered by the sidebar date range and account selection |

### Portfolio tab *(first tab)*

| Section | Description |
|---------|-------------|
| **Account Summary** | One row per account combining position data (Market Value, Cost, Unrealized P&L, Return %, Margin) and transaction data (Net Cash, Dividends, Rewards, Margin Interest, Fees, Net Income) |
| **Sector & Account allocation pies** | Market value breakdown by sector and by account |
| **Positions by Account** | Collapsible per-account grids showing all holdings sorted by market value. Each header shows position count, MV, P&L, and margin at a glance |
| **Sector Summary** | Market value, cost, P&L, allocation %, return % by GICS sector |
| **Yearly pivots** | Account × Year tables for Net Cash Flow, Dividends, Rewards, Margin Interest, and Fees |
| **Crypto Flow** | Coinbase-specific inflow / outflow detail: USD deposits, bank-funded buys, crypto receives / sends |

### Yearly Summary tab

- Year-over-year table (Deposits, Withdrawals, Net Cash, Dividends, Rewards, Margin Interest, Fees, Net Income)
- Income vs Costs stacked bar, Net Income bar, Cash Flow bar, Dividends by account bar
- Drilldown: income by subcategory per year

### Monthly Trends tab

- Dividends & Rewards by month (stacked by account)
- Costs by month (margin interest vs fees)
- Net Cash Flow by month
- Cumulative Income vs Costs line chart

### Transactions tab

- Full filterable / searchable transaction log
- Filter by category, account, year, or description keyword
- Download filtered results as CSV

### Sidebar controls

| Control | Effect |
|---------|--------|
| **Date range** | Filters all transaction data on the page |
| **Accounts** | Limits which accounts appear in tables and charts; defaults to all |
| **Include internal transfers** | Show / hide internal transfer rows |
| **Refresh** | Clears all caches and reruns the app |

---

## 5. Updating data

### Standard broker accounts (Robinhood, Schwab, Webull, etc.)

1. Download a fresh CSV from your broker
2. Replace the file in `activity/`
3. Run `python ingest.py`
4. Click **Refresh** in the dashboard sidebar

### Fidelity yearly summary

The Fidelity CSV contains one row per calendar year. Fidelity updates the
current-year row as the year progresses.

1. Export a fresh "Investment Income" CSV from Fidelity
2. Replace `activity/fidelity_Investment_income_balance.csv`
3. Run `python ingest.py`

The parser handles:
- Updated current-year figures (stable IDs prevent duplication)
- A new year row added at the top for the next calendar year
- Partial-year "As of" dates embedded in the year cell (e.g. `2026(As of Apr-23-2026)`)

Only years from **2020 onwards** are ingested. To change this cutoff, edit
`START_YEAR` at the top of `src/parsers/fidelity.py`.

### Positions workbook

Open `activity/TRADEPOSITIONS.xlsx` in Excel, update positions or let the
live-price formulas recalculate, then save.  The dashboard picks up changes
on the next load or after clicking **Refresh** (cache TTL is 5 minutes).

---

## 6. Adding a new account

1. Write a parser in `src/parsers/<broker>.py` following the pattern of existing parsers.
   Each record must be a dict with these keys:
   ```
   id, account_id, date, category, subcategory,
   amount, currency, symbol, description, source_file
   ```

2. Register it in `ingest.py`:
   ```python
   # In ACCOUNTS list
   {"account_id": "NEW_ACCT", "broker": "newbroker",
    "account_type": "investment", "holder": None},

   # In PARSERS list
   (newbroker.parse, ACTIVITY / "newbroker.csv", "NEW_ACCT"),
   ```

3. Optionally add a sheet to `TRADEPOSITIONS.xlsx` and register it in
   `SHEET_ACCOUNT` at the top of `src/positions.py` (used by both the
   dashboard and the MCP server).

4. Run `python ingest.py`.

---

## 7. MCP Server (Claude Desktop integration)

The MCP server exposes both the transaction database and the positions workbook
as tools that Claude can call from Claude Desktop — no code context needed.

### Available tools

| Tool | Description |
|------|-------------|
| `get_portfolio_summary` | Overall KPIs with optional year/account filter |
| `get_yearly_summary` | Year-over-year breakdown table |
| `get_account_summary` | Per-account breakdown table |
| `get_transactions` | Filterable transaction log (category, account, year, search) |
| `get_positions` | Current holdings from `TRADEPOSITIONS.xlsx` with optional account/sector/type filter |
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
- *"Show me unrealized P&L by sector"*
- *"Which account has the highest return?"*
- *"What is my total market value and margin?"*

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

## 8. Category reference

| Category | Subcategories | Sign |
|----------|--------------|------|
| `cash_flow` | `deposit`, `withdrawal`, `internal_transfer` | + / − |
| `crypto_flow` | `usd_deposit`, `usd_withdrawal`, `bank_purchase`, `crypto_received`, `crypto_sent` | + / − |
| `dividend` | `cash_div`, `manufactured_div`, `reinvested_div`, `nonqualified_div`, `substitute_income`, `prior_yr_div` | + |
| `reward` | `interest`, `staking`, `reward_income`, `platform_reward`, `securities_lending`, `credit_card_reward`, `learning_reward`, `subscription_rebate`, `incentive` | + |
| `margin_interest` | `monthly`, `aggregated_margin` | − |
| `fee` | `trading_fee`, `subscription_fee`, `platform_fee`, `clearing_fee`, `commission` | − |
| `other` | *(filtered out of dashboard)* | |
