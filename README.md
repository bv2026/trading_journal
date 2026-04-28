# Trading Journal

A personal portfolio tracker that consolidates brokerage activity and live
positions across 10 accounts into a single SQLite database and Streamlit
dashboard, with Claude Desktop MCP integration for natural-language queries.

## Accounts

| Account ID       | Broker       | Asset classes tracked         |
|------------------|--------------|-------------------------------|
| RH-BV            | Robinhood    | Equity, margin                |
| RH-KD            | Robinhood    | Equity                        |
| WEBULL           | Webull       | Equity, options               |
| WEBULL-CASH      | Webull       | Equity (cash account)         |
| WEBULL-EVENTS    | Webull       | Event contracts               |
| WEBULL-FUT       | Webull       | Futures                       |
| TS               | TradeStation | Equity, options, futures      |
| SCHWAB           | Schwab       | Equity, options, futures      |
| TRADIER          | Tradier      | Equity, options               |
| FIDELITY         | Fidelity     | Equity                        |
| COINBASE         | Coinbase     | Crypto                        |

## What it tracks

**Transaction history** (ingested from broker CSVs into SQLite):
- **Cash Flow** — deposits and withdrawals
- **Dividends** — cash, reinvested, manufactured, non-qualified
- **Rewards** — staking, interest, securities lending, platform rewards
- **Margin Interest** — monthly charges across all margin accounts
- **Fees** — trading fees, subscription fees, clearing fees
- **Crypto Flow** — Coinbase USD deposits/withdrawals, bank-funded buys, external wallet transfers

**Live positions** (two ingest paths, unified in the DB):
- **CSV path** — equity positions from `positions-*.csv`; live prices fetched from yfinance at dashboard load
- **MCP path** — options, futures, crypto, and equity written directly from broker API responses via `mcp_ingest.py`; sector/industry enriched from yfinance automatically

**Portfolio snapshots** — daily market value per account recorded by `ingest.py`; powers the Performance tab returns (1W / 1M / 3M / YTD / 1Y)

## Dashboard

Single dashboard at `http://localhost:8501`. Six tabs:

| Tab | Contents |
|-----|----------|
| **Portfolio** | Net worth banner · account summary (MV, cost, P&L, margin, income) · sector pie · positions by account with options sub-tables · sector summary · options summary · futures summary |
| **Yearly Summary** | Year-over-year table · income breakdown by type |
| **By Account** | Prev Year / Current Year / ALL pivot tables per account |
| **Positions** | Broker filter · four sub-tabs: Equity (by symbol) · Options (by account) · Futures (by account) · Crypto |
| **Transactions** | Broker filter · filterable/searchable log · CSV export |
| **Performance** | Current value vs 1W ago · return % over 1W / 1M / 3M / YTD / 1Y per account |

## Project structure

```
trading-journal/
├── activity/               Broker CSV exports + positions-*.csv (gitignored)
│   └── archive/            Unused files kept for reference
├── data/                   SQLite database (gitignored)
│   └── journal.db
├── src/
│   ├── db.py               DB helpers — init, upsert, load; all 4 asset classes + snapshots
│   ├── metrics.py          compute_metrics, style helpers
│   ├── positions.py        load_positions_from_db, load_all_positions, yfinance price fetch
│   ├── enrichment.py       enrich_sectors() — fills NULL sector/industry via yfinance
│   ├── parsers/            CSV parsers (transactions + equity positions)
│   │   ├── positions_csv.py
│   │   ├── robinhood.py
│   │   ├── webull.py
│   │   ├── tradestation.py
│   │   ├── schwab.py
│   │   ├── tradier.py
│   │   ├── coinbase.py
│   │   └── fidelity.py
│   └── fetchers/           MCP response normalizers (positions + transactions from broker APIs)
│       ├── base.py         Shared utilities: OCC parsing, currency detection, ID hashing
│       ├── tradier.py
│       ├── tradestation.py
│       ├── webull.py
│       ├── robinhood.py
│       └── schwab.py
├── dashboard/
│   └── app.py              Streamlit dashboard (6 tabs)
├── mcp_server.py           FastMCP server — query tools + refresh_positions write tool
├── mcp_ingest.py           write_* functions — normalize MCP responses → DB
├── ingest.py               CSV ingest pipeline → journal.db; daily portfolio snapshot
├── schema.sql              All tables + 5 SQL views
├── launch-dashboard.vbs    Double-click to start dashboard (no terminal)
├── ingest.vbs              Double-click to run ingest
├── requirements.txt
├── README.md
└── USAGE.md                Full usage guide
```

## Example prompts (Claude Desktop)

**Net worth & positions**
> *"What is my net worth today?"*
> *"Show me my portfolio summary"*
> *"What are my Technology positions in Schwab?"*
> *"Show me all my options positions and their expiry dates"*
> *"What is my total market value including options, futures, and crypto?"*

**Refresh positions from broker APIs**
> *"Refresh my Tradier positions"*
> *"Update Schwab equity and options positions"*
> *"Refresh all positions"*

**Performance**
> *"How has my portfolio performed over the last month?"*
> *"What is my YTD return?"*
> *"Which account has the best 1-year return?"*

**Year-over-year analysis**
> *"Show me dividends year by year"*
> *"Which year had the highest net income?"*
> *"How did 2024 compare to 2023?"*

**Per-account drilldown**
> *"Which account generates the most dividends?"*
> *"Show me a breakdown of all accounts for 2024"*
> *"Find all margin interest charges for RH-BV"*

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
# 2. Drop positions-{account}.csv files into activity/
# 3. Ingest
python ingest.py

# 4. Launch dashboard
streamlit run dashboard/app.py
# — or double-click launch-dashboard.vbs on Windows
```

Dashboard runs at `http://localhost:8501`.

See [USAGE.md](USAGE.md) for full details on file formats, MCP setup, and
the MCP-first positions workflow.
