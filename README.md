# Trading Journal

A personal portfolio tracker that consolidates brokerage activity and live
positions across 8 accounts into a single SQLite database and Streamlit
dashboard.

## Accounts supported

| Account ID | Broker       | Type              |
|------------|--------------|-------------------|
| RH-BV      | Robinhood    | Investment        |
| RH-KD      | Robinhood    | Investment        |
| WEBULL     | Webull       | Investment + Cash |
| TS         | TradeStation | Investment        |
| SCHWAB     | Schwab       | Investment        |
| TRADIER    | Tradier      | Investment        |
| COINBASE   | Coinbase     | Crypto            |
| FIDELITY   | Fidelity     | Investment        |

## What it tracks

**Transaction history** (stored in SQLite, ingested from broker CSVs):
- **Cash Flow** — deposits and withdrawals per account
- **Dividends** — cash, reinvested, manufactured, non-qualified
- **Rewards** — staking, interest, securities lending, platform rewards
- **Margin Interest** — monthly charges across all margin accounts
- **Fees** — trading fees, subscription fees, clearing fees
- **Crypto Flow** — Coinbase USD deposits/withdrawals, bank-funded buys, external wallet transfers

**Live positions** (ingested from per-account CSVs; live prices fetched from yfinance at load time):
- Market value, cost basis, unrealized P&L, return %
- Sector and industry classification
- IV Rank, YTD performance, ATR %
- Margin borrowed per account
- Net worth = total market value − total margin

## Dashboard

The dashboard has five tabs:

| Tab | Contents |
|-----|----------|
| **Portfolio** | Net worth banner · unified account summary · sector allocation pies · positions by account · sector summary · yearly pivots · crypto flow |
| **Yearly Summary** | Year-over-year table · income/cost charts |
| **By Account** | Previous Year / Current Year / ALL pivot tables per account |
| **Positions** | Holdings by symbol — Market Value, Cost, P&L, Sector, Return %, Dividends |
| **Transactions** | Filterable/searchable transaction log with CSV export |

## Project structure

```
trading-journal/
├── activity/               Broker CSV exports + positions-*.csv (gitignored)
├── data/                   SQLite database (gitignored)
│   └── journal.db
├── src/
│   ├── db.py               Database helpers (init, upsert, load; transactions + positions)
│   ├── metrics.py          compute_metrics, style helpers
│   ├── positions.py        load_positions_from_db, sector overrides, yfinance price fetch
│   └── parsers/
│       ├── utils.py        Shared utilities (parse_amount, parse_date, make_id)
│       ├── positions_csv.py  Per-account positions CSV parser
│       ├── robinhood.py
│       ├── webull.py
│       ├── tradestation.py
│       ├── schwab.py
│       ├── tradier.py
│       ├── coinbase.py
│       └── fidelity.py     Yearly summary parser (2020+)
├── dashboard/
│   └── app.py              Streamlit dashboard (5 tabs)
├── mcp_server.py           FastMCP server for Claude Desktop integration
├── ingest.py               Load all CSVs → journal.db (transactions + positions)
├── schema.sql              Table definitions (accounts, transactions, positions)
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
