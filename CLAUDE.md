# Trading Journal — Claude Context

## What This Is
A personal portfolio tracker that consolidates brokerage activity and current positions across 11 accounts into a single SQLite database. Provides a Streamlit dashboard, a FastAPI + Next.js dashboard, a unified CLI, and Claude Desktop MCP integration for natural-language queries.

## Accounts Tracked
| ID | Broker | Asset Classes |
|---|---|---|
| RH-BV | Robinhood | Equity, margin |
| RH-KD | Robinhood | Equity |
| WEBULL | Webull | Equity, options |
| WEBULL-CASH | Webull | Equity (cash) |
| WEBULL-EVENTS | Webull | Event contracts |
| WEBULL-FUT | Webull | Futures |
| TS | TradeStation | Equity, options, futures |
| SCHWAB | Schwab | Equity, options, futures |
| TRADIER | Tradier | Equity, options |
| FIDELITY | Fidelity | Equity |
| COINBASE | Coinbase | Crypto |

## MCP Tools (use these, don't run Python directly)
| Tool | Purpose |
|---|---|
| `journal_get_summary` | Portfolio overview (all accounts) |
| `journal_get_positions` | Current positions |
| `journal_get_positions_enriched` | Positions with sector/industry data |
| `journal_get_performance` | Returns by period (1W/1M/3M/YTD/1Y) |
| `journal_get_pnl` | Realized P&L |
| `journal_get_transactions` | Transaction history |
| `journal_get_income_summary` | Dividends, interest, rewards |
| `journal_get_allocation` | Asset/sector allocation |
| `journal_get_snapshots` | Daily portfolio snapshots |
| `journal_get_accounts` | Account-level summary |
| `journal_ingest_csv` | Ingest broker CSV activity file |
| `journal_enrich_prices` | Refresh prices from yfinance |
| `journal_refresh` | Full sync (positions + prices) |
| `journal_refresh_account` | Sync single account |
| `journal_daily_brief` | Morning summary |
| `journal_update_balance` | Manual balance update |
| `journal_write_snapshot` | Record today's snapshot |

## Two Ingest Paths
- **CSV path** — equity positions from `positions-*.csv`; prices fetched from yfinance
- **MCP path** — options, futures, crypto, equity from broker API responses via `src/mcp_ingest.py`; sector/industry auto-enriched from yfinance

MCP-synced positions are the preferred source for RH-BV, Webull, TradeStation, Schwab, Tradier.

## Key Directories
```
src/           core ingest, MCP server, models
activity/      broker CSV drop zone
dashboard/     Streamlit app
ui/            Next.js dashboard
data/          SQLite database
tests/         pytest suite
scripts/       utilities
```

## Running Tests
```bash
python -m pytest tests/
```

## Important Rules
- Never commit broker CSV activity files — they contain personal financial data
- Broker CSVs go in `activity/` — never commit this directory
- SQLite DB is local only — never push `data/*.db`
- After code changes to `src/`: **restart Claude Desktop** for MCP to pick up changes
