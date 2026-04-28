# Dashboard Redesign — Requirements
> Date: 2026-04-28  
> Scope: MCP-first data layer + account model simplification

---

## 0. Guiding Principles

| # | Principle |
|---|-----------|
| 0.1 | **Additive rollout** — new MCP-powered tabs are added alongside existing tabs; old tabs removed only after new ones are validated |
| 0.2 | **MCP-first prices** — broker MCP position responses embed current prices; use them directly; no separate price fetch needed for MCP accounts |
| 0.3 | **yfinance retained for two purposes only** — (a) sector/industry data (MCPs do not provide this), (b) price fallback for CSV-only accounts (Fidelity, Coinbase) |
| 0.4 | **Positions always full-replace** — each MCP fetch overwrites all position rows for that broker; no stale positions accumulate |
| 0.5 | **Transactions always incremental** — only rows newer than latest DB date per broker are inserted; deduplication on (account_id, date, amount, category) |
| 0.6 | **Dashboard remains read-only** — no order placement from the journal |

---

## 1. Data Source Map (per broker)

| Account | Broker | Equity Positions + Price | Options Positions + Price | Futures Positions + Price | Transactions | Sector Data |
|---------|--------|:------------------------:|:-------------------------:|:-------------------------:|:------------:|:-----------:|
| RH-BV | Robinhood | trayd MCP | trayd MCP | — | CSV only (trayd has no history) | yfinance |
| RH-KD | Robinhood | trayd MCP | trayd MCP | — | CSV only | yfinance |
| WEBULL | Webull | webull MCP | webull MCP | webull MCP | webull MCP | yfinance |
| TS | TradeStation | TS MCP | TS MCP | TS MCP | TS MCP (89d window) | yfinance |
| SCHWAB | Schwab | schwab MCP | schwab MCP | TOS.csv (static) | schwab MCP | yfinance |
| TRADIER | Tradier | Tradier MCP | Tradier MCP | — | Tradier MCP | yfinance |
| FIDELITY | Fidelity | CSV → yfinance price | — | — | CSV | yfinance |
| COINBASE | Coinbase | CSV → yfinance price | — | — | CSV | — |

**Eliminated accounts:** `TRADIER-OPT`, `SCHWAB-OPT` — options merged into parent broker account.  
**Eliminated CSVs:** all equity/options position files; RH/Webull/TS/Tradier/Schwab transaction CSVs.  
**Remaining CSVs:** Fidelity (equity + transactions), Coinbase (crypto + transactions), Schwab TOS (futures positions only).

---

## 2. Live Price Strategy

| Scenario | Price Source | Notes |
|----------|-------------|-------|
| MCP equity position | Embedded in MCP position response | No extra call; trayd returned `current_price` per position |
| MCP options position | Embedded in MCP position response | Includes market value from broker |
| MCP futures position | Embedded in MCP position response | |
| Fidelity equity | yfinance (bulk fetch by ticker) | Same as today |
| Coinbase crypto | yfinance (bulk fetch by ticker) | Same as today |
| Schwab futures (TOS.csv) | Static price stored at ingest | Same as today |
| Sector / industry data | yfinance for all equity tickers | MCPs do not provide sector info |
| Price fallback (MCP unavailable) | yfinance | Graceful degradation |

---

## 3. Account Model Changes

| # | Change | Detail |
|---|--------|--------|
| 3.1 | Retire `TRADIER-OPT` and `SCHWAB-OPT` accounts | Options rows live under `TRADIER` / `SCHWAB` distinguished by `asset_type` |
| 3.2 | Add `asset_type` column to positions table | Values: equity / option / future / crypto |
| 3.3 | Add options columns to positions table | `underlying`, `expiry`, `strike`, `call_put` (null for equity rows) |
| 3.4 | Add `data_source` column to positions + transactions | Values: mcp / csv — audit trail and UI badge |
| 3.5 | `price_source` becomes per-row | live (MCP), yfinance, static (TOS.csv) |
| 3.6 | Existing `options_positions`, `futures_positions`, `crypto_positions` tables | Kept as-is during transition; retired once new positions flow is validated |

---

## 4. Dashboard Tab Rollout Plan

| Phase | Action |
|-------|--------|
| Phase 1 | Add new MCP-powered tabs alongside existing tabs (new names, e.g. "Portfolio v2") |
| Phase 2 | Validate data accuracy and completeness in new tabs |
| Phase 3 | Remove old tabs and rename new ones to replace them |

---

## 5. Tab Requirements

### Tab 1 — Portfolio *(existing, unchanged during Phase 1)*
No changes until Phase 3.

---

### Tab NEW-A — Portfolio v2 *(replaces Tab 1 in Phase 3)*
| # | Requirement | Notes |
|---|-------------|-------|
| A.1 | Total net worth KPI = equity + options + futures + crypto | Single hero number at top |
| A.2 | Asset class breakdown cards: equity / options / futures / crypto | Market value + day P&L per class |
| A.3 | Broker breakdown table: one row per broker, columns = equity MV / options MV / futures MV / total / cost / P&L | Replaces per-account-type split |
| A.4 | Sector allocation pie — equity only | Options/futures excluded |
| A.5 | Sector summary table — equity only, collapsed labels | Same as current |
| A.6 | Options summary: count, total MV, total P&L, expiring this week | New |
| A.7 | Futures summary: count, notional value, unrealized P&L | New |
| A.8 | Data freshness label per broker (last refreshed timestamp + source: MCP / CSV) | Per broker, not global |

---

### Tab 2 — Yearly Summary *(existing, keep as-is)*
Already redesigned. No changes.

---

### Tab 3 — By Account *(existing, unchanged during Phase 1)*
No changes until Phase 3.

---

### Tab NEW-B — By Broker *(replaces Tab 3 in Phase 3)*
| # | Requirement | Notes |
|---|-------------|-------|
| B.1 | One expander section per broker | Collapsed by default |
| B.2 | Each section header: broker name, total MV, total P&L, position count | Scannable at a glance |
| B.3 | Inside each section: equity positions sub-table, options sub-table, futures sub-table (only shown if broker has that asset class) | |
| B.4 | Dividends + income metrics per broker (from transactions) | Same KPIs as current By Account tab |
| B.5 | RH-BV and RH-KD shown as sub-rows within Robinhood section | Two holders under one broker |

---

### Tab 4 — Positions *(existing, unchanged during Phase 1)*
No changes until Phase 3.

---

### Tab NEW-C — Positions v2 *(replaces Tab 4 in Phase 3)*
| # | Requirement | Notes |
|---|-------------|-------|
| C.1 | Single unified table: equity + options + futures + crypto | All asset types in one filterable view |
| C.2 | Filter bar: asset type (all/equity/option/future/crypto) + broker + search by symbol | |
| C.3 | Equity columns: ticker, broker, shares, avg cost, current price, market value, P&L $, P&L %, sector | |
| C.4 | Options columns: symbol, broker, underlying, expiry, strike, C/P, qty, price, market value, P&L | Options-only columns hidden when filter = equity |
| C.5 | Futures columns: symbol, broker, qty, price, market value, P&L | |
| C.6 | Crypto columns: symbol, broker, qty, price, cost basis, market value, P&L | |
| C.7 | Source badge on each row: MCP (green) / CSV (grey) / TOS (orange) | Small indicator, not a full column |
| C.8 | Sort by any column; default sort: market value descending | |

---

### Tab 5 — Transactions *(existing, minor update)*
| # | Requirement | Notes |
|---|-------------|-------|
| 5.1 | No structural change to columns | Keep current category/subcategory model |
| 5.2 | Add `source` filter chip: All / MCP / CSV | Hidden by default; power-user feature |
| 5.3 | Broker filter replaces account filter | Show broker name, not account_id |

---

### Tab 6 — Performance *(existing, minor update)*
| # | Requirement | Notes |
|---|-------------|-------|
| 6.1 | Snapshot-based returns kept (1w, 1m, 3m, ytd, 1y) | Snapshot table written after every refresh |
| 6.2 | Merge `TRADIER-OPT` into `TRADIER` row, `SCHWAB-OPT` into `SCHWAB` | Combined broker-level return |
| 6.3 | No other changes during Phase 1 | Asset class breakdown deferred to Phase 3 |

---

## 6. Ingest / Refresh Architecture

| # | Requirement | Detail |
|---|-------------|--------|
| 6.1 | New `src/fetchers/` directory | One fetcher module per MCP broker: `robinhood.py`, `tradier.py`, `tradestation.py`, `schwab.py`, `webull.py` |
| 6.2 | Each fetcher returns standardised dicts matching the positions + transactions DB schema | Same shape as existing parsers — drop-in compatible |
| 6.3 | MCP-first, CSV fallback | If MCP call fails or broker not connected, fall back to CSV file if present; log which path was taken |
| 6.4 | `ingest.py` orchestrates both old CSV parsers and new MCP fetchers | CSV parsers for RH transactions, Fidelity, Coinbase unchanged |
| 6.5 | Robinhood transactions remain CSV-only | trayd has no transaction history endpoint |
| 6.6 | Schwab futures remain TOS.csv only | schwab MCP has no futures positions tool |
| 6.7 | Snapshot written after every refresh | Aggregate all asset classes → `portfolio_snapshots` |
| 6.8 | Dashboard Refresh button triggers MCP fetches inline | No subprocess restart; fetchers callable directly from dashboard session |
| 6.9 | TradeStation first-run backfill | If TS CSV exists, load it first; thereafter MCP incremental with 89d window |

---

## 7. Schema Changes

| Table / Column | Change | When |
|----------------|--------|------|
| `positions` | Add `asset_type` TEXT (equity/option/future/crypto) | Phase 1 |
| `positions` | Add `underlying`, `expiry`, `strike`, `call_put` TEXT (nullable) | Phase 1 |
| `positions` | Add `data_source` TEXT (mcp/csv) | Phase 1 |
| `positions` | Add `price_source` TEXT (live/yfinance/static) | Phase 1 |
| `transactions` | Add `data_source` TEXT (mcp/csv) | Phase 1 |
| `accounts` | Retire `TRADIER-OPT`, `SCHWAB-OPT` rows | Phase 3 |
| `options_positions` | Deprecate (stop writing; keep for read during transition) | Phase 3 |
| `futures_positions` | Deprecate | Phase 3 |
| `crypto_positions` | Deprecate | Phase 3 |

---

## 8. Out of Scope

| Item | Reason |
|------|--------|
| Real-time streaming prices | MCP tools are request/response only |
| Options Greeks in positions table | Tradier + TradeStation have them; Schwab/Webull do not — inconsistent across brokers |
| Order placement from dashboard | Dashboard is a read-only journal |
| TradeStation history > 89 days | API hard limit; one-time CSV backfill covers the gap |
| Coinbase MCP (balaji-agentkit) | On-chain wallet only; no account transaction history |
| Google Calendar / TradeStation streaming | Not connected / out of scope for journal |
