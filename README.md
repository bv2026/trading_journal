# Trading Journal

A personal portfolio tracker that consolidates brokerage activity and current
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
- **Cash Flow** â€” deposits and withdrawals
- **Dividends** â€” cash, reinvested, manufactured, non-qualified
- **Rewards** â€” staking, interest, securities lending, platform rewards
- **Margin Interest** â€” monthly charges across all margin accounts
- **Fees** â€” trading fees, subscription fees, clearing fees
- **Crypto Flow** â€” Coinbase USD deposits/withdrawals, bank-funded buys, external wallet transfers

**Current positions** (two ingest paths, unified in the DB):
- **CSV path** â€” equity positions from `positions-*.csv`; quote prices fetched from yfinance at dashboard load
- **MCP path** â€” options, futures, crypto, and equity written directly from broker API responses via `src/mcp_ingest.py`; sector/industry enriched from yfinance automatically

**Portfolio snapshots** â€” daily market value per account recorded by `src/ingest.py`; powers the Performance tab returns (1W / 1M / 3M / YTD / 1Y)

MCP-synced positions are the preferred source for RH-BV, Webull, TradeStation,
Schwab, and Tradier. Legacy position CSVs for those accounts are ignored by
normal `python -m src.ingest` runs unless `--include-mcp-position-csv` is passed.

Coinbase spot balances sync through `coinbase-derivatives-mcp`:
```bash
python scripts/sync_coinbase.py
```
This reads the MCP server settings from Claude Desktop config, captures a fresh
Coinbase snapshot with spot USD prices, imports COINBASE crypto positions, and
writes today's journal snapshot.

## Dashboard

Single dashboard at `http://localhost:8501`. Six tabs:

| Tab | Contents |
|-----|----------|
| **Portfolio** | Net worth banner Â· account summary (MV, cost, P&L, margin, income) Â· sector pie Â· positions by account with options sub-tables Â· sector summary Â· options summary Â· futures summary |
| **Yearly Summary** | Year-over-year table Â· income breakdown by type |
| **By Account** | Prev Year / Current Year / ALL pivot tables per account |
| **Positions** | Broker filter Â· four sub-tabs: Equity (by symbol) Â· Options (by account) Â· Futures (by account) Â· Crypto |
| **Transactions** | Broker filter Â· filterable/searchable log Â· CSV export |
| **Performance** | Current value vs 1W ago Â· return % over 1W / 1M / 3M / YTD / 1Y per account |

## Project structure

```
trading-journal/
â”œâ”€â”€ activity/               Broker CSV exports + positions-*.csv (gitignored)
â”‚   â””â”€â”€ archive/            Unused files kept for reference
â”œâ”€â”€ data/                   SQLite database (gitignored)
â”‚   â””â”€â”€ journal.db
â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ db.py               DB helpers â€” init, upsert, load; all 4 asset classes + snapshots
â”‚   â”œâ”€â”€ metrics.py          compute_metrics, style helpers
â”‚   â”œâ”€â”€ positions.py        load_positions_from_db, load_all_positions, yfinance price fetch
â”‚   â”œâ”€â”€ enrichment.py       enrich_sectors() â€” fills NULL sector/industry via yfinance
â”‚   â”œâ”€â”€ parsers/            CSV parsers (transactions + equity positions)
â”‚   â”‚   â”œâ”€â”€ positions_csv.py
â”‚   â”‚   â”œâ”€â”€ robinhood.py
â”‚   â”‚   â”œâ”€â”€ webull.py
â”‚   â”‚   â”œâ”€â”€ tradestation.py
â”‚   â”‚   â”œâ”€â”€ schwab.py
â”‚   â”‚   â”œâ”€â”€ tradier.py
â”‚   â”‚   â”œâ”€â”€ coinbase.py
â”‚   â”‚   â””â”€â”€ fidelity.py
â”‚   â””â”€â”€ fetchers/           MCP response normalizers (positions + transactions from broker APIs)
â”‚       â”œâ”€â”€ base.py         Shared utilities: OCC parsing, currency detection, ID hashing
â”‚       â”œâ”€â”€ tradier.py
â”‚       â”œâ”€â”€ tradestation.py
â”‚       â”œâ”€â”€ webull.py
â”‚       â”œâ”€â”€ robinhood.py
â”‚       â””â”€â”€ schwab.py
â”œâ”€â”€ dashboard/
â”‚   â””â”€â”€ app.py              Streamlit dashboard
â”œâ”€â”€ scripts/                Operator utilities (MCP auth, Coinbase sync)
â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ mcp_server.py       FastMCP server â€” query tools + refresh_positions write tool
â”‚   â”œâ”€â”€ mcp_ingest.py       write_* functions â€” normalize MCP responses â†’ DB
â”‚   â”œâ”€â”€ ingest.py           CSV ingest pipeline â†’ journal.db; daily portfolio snapshot
â”‚   â”œâ”€â”€ journal_cli.py      Terminal account/position browser
│   ├── schema.sql          All tables + SQL views
â”‚   â””â”€â”€ ...
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ README.md
â””â”€â”€ docs/USAGE.md           Full usage guide
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
python -m src.ingest

python -m src.journal_cli

# 4. Launch dashboard
streamlit run dashboard/app.py
# Or use python -m src.journal_cli -> Housekeeping -> Launch dashboard
```

The CLI checks configured broker MCP server health before showing account
balances. The balances themselves are still DB-backed and reflect the last
completed sync; health only confirms the MCP server is reachable.

Remote MCP health checks use these defaults and can be overridden with env vars:
`ROBINHOOD_MCP_URL`, `TRADESTATION_MCP_URL`, `TRADIER_MCP_URL`. If a remote
server requires bearer auth outside the connector UI, set
`ROBINHOOD_MCP_BEARER_TOKEN`, `TRADESTATION_MCP_BEARER_TOKEN`, or
`TRADIER_MCP_BEARER_TOKEN`.

Remote OAuth MCPs can be authorized from PowerShell:
```bash
python scripts/authorize_mcp.py tradestation --manual
python scripts/authorize_mcp.py robinhood --manual
```
Follow the printed URL, then paste the final callback URL or authorization code
back into the terminal. Saved tokens live in `data/mcp_tokens/`.

Robinhood live balance checks enumerate every Trayd profile under `~/.trayd`
and every account returned by `list_accounts`. Map Robinhood account numbers to
journal account IDs in ignored `data/config/robinhood_accounts.json`, for example
`{"869439976": "RH-BV", "123456789": "RH-KD"}`, or set
`ROBINHOOD_ACCOUNT_MAP` to the same JSON.

Trayd has two auth layers for Robinhood:
```bash
python -m src.cli.robinhood --login --profile bv          # Trayd OAuth token
python -m src.cli.robinhood --link-robinhood --profile bv # Robinhood email/password + app approval
python -m src.cli.robinhood --status --profile bv
python -m src.cli.robinhood --logout-robinhood --profile bv
```
`--link-robinhood` prompts for the Robinhood password at runtime and never
writes it to disk.

Dashboard runs at `http://localhost:8501`.

See [USAGE.md](docs/USAGE.md) for full details on file formats, MCP setup, and
the MCP-first positions workflow.
