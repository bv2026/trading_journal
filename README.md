# Trading Journal

A personal portfolio tracker that consolidates brokerage activity and live
positions across 8 accounts into a single SQLite database and Streamlit
dashboard.

## Accounts supported

| Account ID  | Broker       | Asset class       |
|-------------|--------------|-------------------|
| RH-BV       | Robinhood    | Equity            |
| RH-KD       | Robinhood    | Equity            |
| WEBULL      | Webull       | Equity            |
| TS          | TradeStation | Equity            |
| SCHWAB      | Schwab       | Equity            |
| TRADIER     | Tradier      | Equity            |
| TRADIER-OPT | Tradier      | Options           |
| SCHWAB-OPT  | Schwab       | Options           |
| COINBASE    | Coinbase     | Crypto            |
| FIDELITY    | Fidelity     | Equity            |

## What it tracks

**Transaction history** (stored in SQLite, ingested from broker CSVs):
- **Cash Flow** — deposits and withdrawals per account
- **Dividends** — cash, reinvested, manufactured, non-qualified
- **Rewards** — staking, interest, securities lending, platform rewards
- **Margin Interest** — monthly charges across all margin accounts
- **Fees** — trading fees, subscription fees, clearing fees
- **Crypto Flow** — Coinbase USD deposits/withdrawals, bank-funded buys, external wallet transfers

**Live equity positions** (ingested from per-account CSVs; live prices fetched from yfinance at load time):
- Market value, cost basis, unrealized P&L, return %
- Sector and industry classification
- IV Rank, YTD performance, ATR %
- Margin borrowed per account
- Net worth = total market value − total margin

**Options / futures / crypto positions** (stored at ingest time from Tradier-style CSV exports):
- Market value, quantity, strike, expiry, call/put
- Unified with equity in net worth and performance calculations

## Dashboard

The dashboard has six tabs:

| Tab | Contents |
|-----|----------|
| **Portfolio** | Net worth banner · unified account summary · sector allocation pies · positions by account · sector summary |
| **Yearly Summary** | Year-over-year table · income/cost charts |
| **By Account** | Previous Year / Current Year / ALL pivot tables per account |
| **Positions** | Holdings by symbol — Market Value, Cost, P&L, Sector, Return %, Dividends |
| **Transactions** | Filterable/searchable transaction log with CSV export |
| **Performance** | Portfolio Summary (current value, margin, 1W change) · Portfolio Returns (1W / 1M / 3M / YTD / 1Y) per account |

## Project structure

```
trading-journal/
├── activity/               Broker CSV exports + positions-*.csv (gitignored)
├── data/                   SQLite database (gitignored)
│   └── journal.db
├── src/
│   ├── db.py               DB helpers — init, upsert, load; all 4 asset classes + snapshots
│   ├── metrics.py          compute_metrics, style helpers
│   ├── positions.py        load_positions_from_db, load_all_positions, yfinance price fetch
│   └── parsers/
│       ├── utils.py        Shared utilities (parse_amount, parse_date, make_id)
│       ├── positions_csv.py        Per-account equity positions CSV parser
│       ├── static_positions_csv.py Options / futures / crypto CSV parser (Tradier-style)
│       ├── robinhood.py
│       ├── webull.py
│       ├── tradestation.py
│       ├── schwab.py
│       ├── tradier.py
│       ├── coinbase.py
│       └── fidelity.py     Yearly summary parser (2020+)
├── dashboard/
│   └── app.py              Streamlit dashboard (6 tabs, including Performance)
├── mcp_server.py           FastMCP server for Claude Desktop integration
├── ingest.py               Load all CSVs → journal.db; writes daily portfolio snapshot
├── schema.sql              Tables: accounts, transactions, positions (equity/options/futures/crypto),
│                           portfolio_snapshots; views: v_snapshot_periods + 4 others
├── requirements.txt
├── README.md
└── USAGE.md                Full usage guide
```

## Example prompts (Claude Desktop)

Once the MCP server is registered you can ask Claude questions directly in chat.

**Net worth & positions**
> *"What is my net worth today?"*
> *"Show me my portfolio summary"*
> *"What are my Technology positions in Schwab?"*
> *"Show me unrealized P&L by sector"*
> *"Show me all my options positions"*
> *"What is my total market value including options and crypto?"*

**Portfolio performance**
> *"How has my portfolio performed over the last month?"*
> *"What is my YTD return?"*
> *"Which account has the best 1-year return?"*
> *"Show me my 1-week return for each account"*

**Portfolio overview**
> *"What is my all-time net income across all accounts?"*
> *"How much have I paid in margin interest over the years?"*

**Year-over-year analysis**
> *"Show me dividends year by year"*
> *"Which year had the highest net income?"*
> *"How did 2024 compare to 2023 for fees and margin interest?"*

**Per-account drilldown**
> *"How is Fidelity performing vs Robinhood?"*
> *"Which account generates the most dividends?"*
> *"Show me a breakdown of all accounts for 2024"*

**Transactions**
> *"Show me all Coinbase staking rewards"*
> *"List my largest dividends in 2024"*
> *"Find all margin interest charges for RH-BV"*
> *"Show me recent withdrawals across all accounts"*

**Data management**
> *"Ingest the latest files"*
> *"Launch the dashboard"*

---

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Quick start

```bash
# 1. Drop broker CSV exports into activity/
# 2. Drop positions-{account}.csv files into activity/ (see USAGE.md)
# 3. Ingest transactions + positions
python ingest.py

# 4. Launch dashboard
streamlit run dashboard/app.py
```

Dashboard runs at `http://localhost:8501`.

See [USAGE.md](USAGE.md) for full details on file formats, MCP setup, and
adding new accounts.
