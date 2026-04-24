# Usage Guide

## Prerequisites

- Python 3.11+
- Dependencies installed (`pip install -r requirements.txt`), including `mcp>=1.0.0`

---

## 1. Prepare broker CSV files

Place all broker exports in the `activity/` folder with the exact filenames below.  
The folder is gitignored — files never leave your machine.

| File | Broker | Parser |
|------|--------|--------|
| `robinhood-inv-bv.csv` | Robinhood (BV account) | robinhood.py |
| `robinhood-inv-kd.csv` | Robinhood (KD account) | robinhood.py |
| `WEBULL-inv.csv` | Webull investment | webull.py |
| `WEBULL-cash.csv` | Webull cash | webull.py |
| `tdstation-cash.csv` | TradeStation cash activity | tradestation.py |
| `schwab.csv` | Schwab | schwab.py |
| `tradier.csv` | Tradier | tradier.py |
| `coinbase-main.csv` | Coinbase | coinbase.py |
| `fidelitry_Investment_income_balance.csv` | Fidelity yearly summary | fidelity.py |

Missing files are skipped with a warning — you do not need all files present.

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
Initializing database …
Clearing existing transactions …
Upserting accounts …
  OK    RH-BV:  1796 records
  OK    WEBULL:   387 records
  OK    FIDELITY:  28 records
  ...
Total: 16566 records (0 duplicates removed)
Ingest complete.
```

---

## 3. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`.  
Use the **🔄 Refresh** button in the sidebar after re-ingesting to reload data.

---

## 4. Dashboard tabs

### By Account
- **Account Summary** — all-time totals per account (Net Cash, Dividends, Rewards, Margin Interest, Fees, Net Income)
- **Pivot tables** — each metric broken down by Account × Year with an ALL column and TOTAL row:
  - Net Cash Flow
  - Dividends
  - Rewards
  - Margin Interest
  - Fees
- **Crypto Flow detail** — Coinbase-specific inflow/outflow breakdown (USD deposits, USDC trades, crypto sends/receives)

### Yearly Summary
- Year-over-year table across all accounts
- Income vs Costs chart, Net Income chart, Cash Flow chart, Dividends by account

### Monthly Trends
- Dividends & Rewards by month
- Costs by month
- Net Cash Flow by month
- Cumulative Income vs Costs

### Transactions
- Filterable/searchable transaction log
- Filter by category, account, year, or description keyword
- Download filtered results as CSV

---

## 5. Updating data

### Standard broker accounts (Robinhood, Schwab, etc.)
1. Download a fresh CSV from your broker
2. Replace the file in `activity/`
3. Run `python ingest.py`
4. Click **🔄 Refresh** in the dashboard sidebar

### Fidelity yearly summary
The Fidelity CSV contains one row per calendar year.  Fidelity updates the
current-year row (top of the file) as the year progresses.

1. Export a fresh "Investment Income" CSV from Fidelity
2. Replace `activity/fidelitry_Investment_income_balance.csv`
3. Run `python ingest.py`

The parser automatically handles:
- Updated current-year figures (stable IDs prevent duplication)
- A new year row added at the top for the next calendar year
- Partial-year "As of" dates embedded in the year cell

Only years from **2020 onwards** are ingested. To change this cutoff, edit
`START_YEAR` at the top of `src/parsers/fidelity.py`.

---

## 6. Adding a new account

1. Write a parser in `src/parsers/<broker>.py` following the pattern of existing parsers.  
   Each record must be a dict with keys:  
   `id, account_id, date, category, subcategory, amount, currency, symbol, description, source_file`

2. Register it in `ingest.py`:
   ```python
   # In ACCOUNTS list
   {"account_id": "NEW_ACCT", "broker": "newbroker", "account_type": "investment", "holder": None},

   # In PARSERS list
   (newbroker.parse, ACTIVITY / "newbroker.csv", "NEW_ACCT"),
   ```

3. Run `python ingest.py`.

---

## 7. MCP Server (ask Claude questions outside of code)

The MCP server exposes the journal database as tools that Claude can call
from Claude Desktop or any MCP-compatible client — no code context needed.

### Available tools

| Tool | Description |
|------|-------------|
| `get_portfolio_summary` | Overall KPIs with optional year/account filter |
| `get_yearly_summary` | Year-over-year breakdown table |
| `get_account_summary` | Per-account breakdown table |
| `get_transactions` | Filterable transaction log (category, account, year, search) |
| `run_ingest` | Re-load all broker CSVs into the database |

### Register with Claude Desktop

1. Open (or create) `%APPDATA%\Claude\claude_desktop_config.json`
2. Add the following entry under `mcpServers`:

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

- *"What were my total dividends in 2024?"*
- *"Compare my net cash flow year over year"*
- *"Show me all Coinbase staking rewards"*
- *"What's my net income across all accounts?"*
- *"Ingest the latest CSV files"* → triggers `run_ingest`

### Updating data via Claude

Drop updated CSVs into `activity/` and ask Claude:
> *"Please ingest the latest files"*

Claude will call `run_ingest()` and confirm the record counts.

---

## 8. Category reference

| Category | Subcategories | Sign |
|----------|--------------|------|
| `cash_flow` | `deposit`, `withdrawal`, `internal_transfer` | + / − |
| `crypto_flow` | `usd_deposit`, `usd_withdrawal`, `usdc_purchased`, `usdc_sold`, `crypto_received`, `crypto_sent` | + / − |
| `dividend` | `cash_div`, `manufactured_div`, `reinvested_div`, `nonqualified_div`, `substitute_income`, `prior_yr_div` | + |
| `reward` | `interest`, `staking`, `reward_income`, `platform_reward`, `securities_lending`, `credit_card_reward`, `learning_reward`, `subscription_rebate`, `incentive` | + |
| `margin_interest` | `monthly`, `aggregated_margin` | − |
| `fee` | `trading_fee`, `subscription_fee`, `platform_fee`, `clearing_fee`, `commission` | − |
| `other` | *(filtered out of dashboard)* | |
