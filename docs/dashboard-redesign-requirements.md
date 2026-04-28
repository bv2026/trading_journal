# Dashboard Redesign — Requirements
> Date: 2026-04-28  
> Scope: MCP-first data layer + account model simplification

---

## 1. Data Source Map (per broker)

| Account | Broker | Equity Positions | Options Positions | Futures Positions | Transactions |
|---------|--------|:----------------:|:-----------------:|:-----------------:|:------------:|
| RH-BV | Robinhood | trayd MCP | trayd MCP | — | CSV (no history in MCP) |
| RH-KD | Robinhood | trayd MCP | trayd MCP | — | CSV (no history in MCP) |
| WEBULL | Webull | webull MCP | webull MCP | webull MCP | webull MCP |
| TS | TradeStation | TS MCP | TS MCP | TS MCP | TS MCP (89d window) |
| SCHWAB | Schwab | schwab MCP | schwab MCP | TOS.csv | schwab MCP |
| TRADIER | Tradier | Tradier MCP | Tradier MCP | — | Tradier MCP |
| FIDELITY | Fidelity | CSV | — | — | CSV |
| COINBASE | Coinbase | CSV | — | — | CSV |

**Eliminated accounts:** `TRADIER-OPT`, `SCHWAB-OPT` — options are now merged into their parent broker accounts.  
**Eliminated CSVs:** all equity and options position files; Robinhood, Webull, TradeStation, Tradier, and Schwab equity transaction CSVs.  
**Remaining CSVs:** Fidelity (equity + transactions), Coinbase (crypto + transactions), Schwab TOS (futures positions only).

---

## 2. Account Model Changes

| # | Change | Detail |
|---|--------|--------|
| 1 | Remove separate options accounts | `TRADIER-OPT` and `SCHWAB-OPT` retired; options rows live under `TRADIER` and `SCHWAB` in the unified positions table |
| 2 | Add `asset_type` column to positions | Each position row carries `asset_type` ∈ {equity, option, future, crypto} instead of account-level `account_type` |
| 3 | `price_source` → live for all MCP accounts | Equity + options prices fetched live from broker MCP at load time; TOS.csv futures remain static at ingest |
| 4 | Transactions scope | RH transactions remain CSV-only; all other MCP brokers provide incremental transactions via API |

---

## 3. Dashboard Tab Requirements

### Tab 1 — Portfolio (Net Worth)
| # | Requirement | Notes |
|---|-------------|-------|
| 1.1 | Total net worth KPI = equity + options + futures + crypto | Single number at top |
| 1.2 | Breakdown by asset class: equity / options / futures / crypto | Card row or bar |
| 1.3 | Breakdown by broker (all accounts consolidated per broker) | Replaces per-account-type split |
| 1.4 | Sector allocation pie — equity only | Options/futures excluded from sector chart |
| 1.5 | Sector summary table — equity only, collapsed labels | Current behaviour; keep |
| 1.6 | Options summary sub-section | Count of open positions, total market value, total P&L |
| 1.7 | Futures summary sub-section | Count, notional value, unrealized P&L |
| 1.8 | Data freshness indicator per broker | Show last-refresh timestamp; MCP = near-real-time, CSV = last ingest |

### Tab 2 — Yearly Summary
| # | Requirement | Notes |
|---|-------------|-------|
| 2.1 | Transposed table: metrics as rows, [prior year / current year / ALL] as columns | Already redesigned — keep |
| 2.2 | Income breakdown by type table | Already added — keep |
| 2.3 | No year charts | Removed in last commit — keep removed |

### Tab 3 — By Account
| # | Requirement | Notes |
|---|-------------|-------|
| 3.1 | One section per broker (not per account-type sub-account) | TRADIER shows equity + options together; SCHWAB shows equity + options + futures |
| 3.2 | Each broker section: balance, positions count, market value, cost, P&L, dividends | Same KPIs as today |
| 3.3 | Expand/collapse per broker | Keep current accordion or similar |

### Tab 4 — Positions
| # | Requirement | Notes |
|---|-------------|-------|
| 4.1 | Unified table: equity + options + futures + crypto | Single filterable table |
| 4.2 | Asset type filter | equity / option / future / crypto |
| 4.3 | Broker filter | replaces account filter |
| 4.4 | Options rows show: symbol, underlying, expiry, strike, call/put, qty, price, market value, P&L | Options-specific columns hidden for equity rows |
| 4.5 | Futures rows show: symbol, qty, price, market value, P&L | |
| 4.6 | Equity rows show: ticker, shares, avg cost, current price, market value, P&L, sector | |
| 4.7 | Live price badge on MCP-sourced rows | Distinguish from static (TOS.csv / CSV) prices |

### Tab 5 — Transactions
| # | Requirement | Notes |
|---|-------------|-------|
| 5.1 | No change to column structure | Keep current category/subcategory model |
| 5.2 | Source column (mcp / csv) | Useful for auditing; can be hidden by default |
| 5.3 | Broker filter | replaces account filter |

### Tab 6 — Performance
| # | Requirement | Notes |
|---|-------------|-------|
| 6.1 | Snapshot-based returns kept (1w, 1m, 3m, ytd, 1y) | Snapshot table is still written at each ingest/refresh |
| 6.2 | Performance rows per broker (not per sub-account) | TRADIER-OPT merged into TRADIER |
| 6.3 | Asset class breakdown for brokers with mixed positions | Show equity vs options contribution to return where data allows |

---

## 4. Ingest / Refresh Architecture

| # | Requirement | Detail |
|---|-------------|--------|
| 4.1 | MCP-first, CSV fallback | Each broker fetcher tries MCP; if unavailable falls back to CSV file if present |
| 4.2 | Positions always full-replace per broker | MCP positions overwrite all rows for that broker on each refresh |
| 4.3 | Transactions incremental | Only insert rows newer than latest DB date for that broker; deduplicate on (account_id, date, amount, category) |
| 4.4 | Robinhood transactions remain CSV-only | trayd has no transaction history; CSV parsers for RH-BV, RH-KD unchanged |
| 4.5 | Schwab futures = TOS.csv only | schwab MCP has no futures positions; TOS.csv import unchanged |
| 4.6 | Fidelity + Coinbase = CSV only | No MCP available; parsers unchanged |
| 4.7 | Snapshot written after every refresh | Same logic as today — aggregate equity (live MCP price) + options + futures + crypto → portfolio_snapshots |
| 4.8 | Refresh callable from dashboard | Button triggers MCP fetches + snapshot; no full restart needed |
| 4.9 | TradeStation 89-day history limit | On first connect, backfill from CSV if available; thereafter MCP incremental |

---

## 5. Schema Changes Required

| Table / Column | Change | Reason |
|----------------|--------|--------|
| `accounts` | Remove `TRADIER-OPT`, `SCHWAB-OPT` rows | Merged into parent broker |
| `positions` | Add `asset_type` column (equity/option/future/crypto) | Replace account-level type |
| `positions` | Add options columns: `underlying`, `expiry`, `strike`, `call_put` | Options in same table |
| `positions` | Add `price_source` column (live/static/tos_csv) | Per-row data freshness |
| `positions` | Add `data_source` column (mcp/csv) | Audit trail |
| `transactions` | Add `data_source` column (mcp/csv) | Audit trail |
| `options_positions` | **Deprecate** — merge into `positions` | Simplify schema |
| `futures_positions` | Keep separate OR merge into `positions` with asset_type=future | TBD |
| `crypto_positions` | Keep separate OR merge into `positions` with asset_type=crypto | TBD |

---

## 6. Out of Scope (this redesign)

| Item | Reason |
|------|--------|
| Real-time streaming / websocket prices | MCP tools are request/response only |
| Options Greeks in positions table | Tradier + TradeStation MCPs have Greeks; Schwab/Webull do not — inconsistent |
| Automated order placement from dashboard | Dashboard remains read-only journal |
| TradeStation transaction history > 89 days | API limitation; CSV backfill is one-time manual step |
| Coinbase MCP (balaji-agentkit) | On-chain only; does not expose account transaction history |
