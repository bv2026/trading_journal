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

## 5. Global / Page-Level Elements *(apply across all tabs)*

| # | Element | Current Behaviour | Redesign Change |
|---|---------|------------------|-----------------|
| G.1 | **Sidebar date range** | Filters transactions; positions unaffected | No change — positions are always point-in-time |
| G.2 | **Sidebar account selector** | Multiselect by account_id | Phase 3: change to broker selector; during Phase 1 keep account_id |
| G.3 | **Sidebar "Include internal transfers"** | Toggles internal_transfer subcategory | Keep as-is |
| G.4 | **Header KPI bar** | Single row: Net Cash Flow, Dividends, Rewards, Div+Rewards, Margin Interest, Fees, Net Income (transaction-based, date+account filtered) | No change — all from transactions table |
| G.5 | **Net Worth banner** | 3 metrics: Net Worth / Market Value / Margin Borrowed — equity positions only | Expand to include options + futures + crypto in Market Value total; Margin Borrowed stays equity-only |
| G.6 | **Refresh button** | Triggers full cache clear + rerun | Phase 1: keep; Phase 2: trigger MCP fetches inline without subprocess restart |

---

## 6. Tab Requirements

### Tab 1 — Portfolio *(existing, unchanged during Phase 1)*
No changes until Phase 3.

---

### Tab NEW-A — Portfolio v2 *(replaces Tab 1 in Phase 3)*
| # | Requirement | Notes |
|---|-------------|-------|
| A.1 | Total net worth KPI = equity + options + futures + crypto | Single hero number at top |
| A.2 | Asset class breakdown cards: equity / options / futures / crypto | Market value + day P&L per class |
| A.3 | Account Summary table — one row per broker/account, same columns as today | Adds options MV + futures MV columns; retires TRADIER-OPT and SCHWAB-OPT rows |
| A.4 | Sector allocation pie — equity only, ETF collapsed | Same as current |
| A.5 | Positions by Account expanders — equity positions per broker | Same as current; drop PERF_YTD / IV_Rank / ATR_pct columns (no MCP source) |
| A.6 | Sector summary table — equity only, collapsed labels | Same as current |
| A.7 | Options summary sub-section: count, total MV, total P&L, expiring this week | New |
| A.8 | Futures summary sub-section: count, notional value, unrealized P&L | New |
| A.9 | Data freshness label per broker (last refreshed + source: MCP / CSV) | Per broker row in Account Summary |

> **Note on dropped columns:** `PERF_YTD`, `IV_Rank`, `ATR_pct` currently come from position CSV files. No broker MCP provides these. They will be dropped from the positions expander in the redesign.

---

### Tab 2 — Yearly Summary *(existing, keep as-is)*
Already redesigned. No changes.

---

### Tab 3 — By Account *(existing, unchanged during Phase 1)*
Current content:
- **Net Cash Flow by Account** — pivot table: Account × (prev year / curr year / ALL)
- **Div + Rewards by Account** — same pivot
- **Margin + Fees by Account** — same pivot
- **Crypto Flow (Coinbase)** — inflow/outflow breakdown: usd_deposit, bank_purchase, crypto_received, usd_withdrawal, crypto_sent; 3-column layout (Inflows / Outflows / Net metrics)

No changes until Phase 3.

---

### Tab NEW-B — By Broker *(replaces Tab 3 in Phase 3)*
| # | Requirement | Notes |
|---|-------------|-------|
| B.1 | Net Cash Flow / Div+Rewards / Margin+Fees pivot tables | Keep exactly; change row label from account_id to broker name |
| B.2 | Crypto Flow section | Keep as-is; still sourced from Coinbase CSV |
| B.3 | One expander section per broker for position drill-down | Collapsed by default; header shows broker, total MV, P&L, position count |
| B.4 | Inside each expander: equity sub-table, options sub-table, futures sub-table (hidden if empty) | |
| B.5 | RH-BV and RH-KD shown as separate rows within Robinhood expander | Two holders, one broker |

---

### Tab 4 — Positions *(existing, unchanged during Phase 1)*
Current content:
- **Positions by Symbol** table — aggregated across all accounts by ticker; columns: Ticker, Name, sector, Market_Value, Total_Cost, PnL, Return_%, Dividends (lifetime, from full transaction history, not date-filtered)
- **Footer totals** — 5 metric tiles: Market Value, Total Cost, P&L, Return, Dividends
- Note: this tab aggregates by symbol (multi-account holders of same stock are summed)

No changes until Phase 3.

---

### Tab NEW-C — Positions v2 *(replaces Tab 4 in Phase 3)*
Four separate sub-tables within the tab, one per asset class. Broker filter at the top applies to all sub-tables.

| # | Requirement | Notes |
|---|-------------|-------|
| C.1 | Broker filter at top — applies to all sub-tables | Replaces account filter |
| C.2 | **Equity sub-table** columns: ticker, broker, shares, avg cost, current price, market value, P&L $, P&L %, sector, lifetime dividends | Aggregated by symbol across accounts (same as current); default sort: market value descending |
| C.3 | **Equity sub-table** footer: Market Value, Total Cost, P&L, Return %, Dividends tiles | Same 5-metric footer as current Tab 4 |
| C.4 | **Options sub-table** columns: symbol, broker, underlying, expiry, strike, C/P, qty, price, market value, P&L | Default sort: expiry ascending |
| C.5 | **Futures sub-table** columns: symbol, broker, qty, price, market value, P&L | |
| C.6 | **Crypto sub-table** columns: symbol, broker, qty, price, cost basis, market value, P&L | |
| C.7 | Each sub-table hidden if no positions in that class | No empty tables shown |
| C.8 | Source badge on each row: MCP (green) / CSV (grey) / TOS (orange) | Small tag, not a full column |
| C.9 | Sub-table totals row: market value + P&L summed | Footer row per sub-table |

---

### Tab 5 — Transactions *(existing, minor update)*
Current content:
- Filters: Category multiselect, Account multiselect, Year multiselect, Description text search
- Columns: date, account_id, broker, category, subcategory, amount, currency, symbol, description
- Row count caption
- **Download CSV button**

| # | Requirement | Notes |
|---|-------------|-------|
| 5.1 | Keep all current columns including currency | No structural change |
| 5.2 | Keep Download CSV button | Already present; keep |
| 5.3 | Add `source` filter chip: All / MCP / CSV | Hidden by default; power-user feature |
| 5.4 | Broker filter replaces account filter in Phase 3 | Show broker name, not account_id |

---

### Tab 6 — Performance *(existing, minor update)*
Current content:
- **Portfolio Summary** table — per account: Current Value, 1W Ago, $ Change, % Change
- **Portfolio Returns** table — per account: 1-Week, 1-Month, 3-Month, YTD, 1-Year
- Performance uses **net_value = current_value − margin** (margin-adjusted); this must be preserved
- Historical snapshots accumulate with each ingest run; caption shown when no history yet

| # | Requirement | Notes |
|---|-------------|-------|
| 6.1 | Keep both sub-sections (Portfolio Summary + Portfolio Returns) | No structural change |
| 6.2 | Net value = current MV − margin borrowed; keep this calculation | Critical — ensures margin accounts show true equity |
| 6.3 | Merge `TRADIER-OPT` into `TRADIER` row, `SCHWAB-OPT` into `SCHWAB` | Combined broker-level return in Phase 3 |
| 6.4 | Snapshots written after every refresh must include options + futures + crypto MV | Currently equity-only; needs to aggregate all asset classes |
| 6.5 | No other changes during Phase 1 | Asset class breakdown deferred to Phase 3 |

---

## 6. Instrument Master Table

A new `instruments` table acts as a **security master / symbol catalog** — the single source of truth for all static metadata about every ticker, option, future, or crypto symbol seen in the system. Key principle: **fetch once, reuse forever** for anything that does not change.

### Schema

```sql
CREATE TABLE instruments (
    -- ── Identity ──────────────────────────────────────────────────────────────
    symbol          TEXT PRIMARY KEY,  -- canonical symbol (AAPL, /ESM25, BTC, AAPL250516C00200000)
    name            TEXT,              -- human-readable name ("Apple Inc.", "E-mini S&P 500 Jun 2025")
    asset_class     TEXT NOT NULL,     -- Stock | ETF | Option | Future | Crypto
    exchange        TEXT,              -- NASDAQ | NYSE | CME | NYMEX | CBOE | Coinbase …
    currency        TEXT DEFAULT 'USD',
    country         TEXT,              -- equities only (US, GB, …)
    is_active       INTEGER DEFAULT 1, -- 0 = expired option/future or delisted

    -- ── Equity / ETF fields ───────────────────────────────────────────────────
    sector          TEXT,              -- yfinance sector (Technology, Healthcare …)
    industry        TEXT,              -- yfinance industry (Semiconductors, Biotech …)
    etf_category    TEXT,              -- ETF only: "Large Blend", "Technology" (yfinance fund_category)
    etf_fund_family TEXT,              -- ETF only: iShares | Vanguard | SPDR …
    etf_expense_ratio REAL,           -- ETF only: annual expense ratio (0.0003 = 0.03%)
    etf_tracking_index TEXT,           -- ETF only: "S&P 500", "Russell 2000" …
    inception_date  TEXT,              -- ETF/Stock IPO date (YYYY-MM-DD)

    -- ── Option fields ─────────────────────────────────────────────────────────
    underlying      TEXT,              -- Options + Futures: parent symbol (AAPL, /ES)
    expiry_date     TEXT,              -- Options + Futures: expiration (YYYY-MM-DD)
    strike          REAL,              -- Options only: strike price
    call_put        TEXT,              -- Options only: C | P
    option_style    TEXT,              -- Options only: American | European
    contract_size   INTEGER,           -- Options: 100 | Futures: contract multiplier (e.g. 50 for /ES)

    -- ── Futures fields ────────────────────────────────────────────────────────
    futures_root    TEXT,              -- root symbol without month/year (/ES, /CL, /ZW)
    contract_month  TEXT,              -- e.g. "Jun 2025"
    tick_size       REAL,              -- minimum price increment (0.25 for /ES)
    tick_value      REAL,              -- dollar value per tick ($12.50 for /ES)
    first_notice_date TEXT,            -- YYYY-MM-DD
    last_trading_date TEXT,            -- YYYY-MM-DD

    -- ── Crypto fields ─────────────────────────────────────────────────────────
    blockchain      TEXT,              -- Bitcoin | Ethereum | Solana …
    max_supply      REAL,              -- maximum token supply (21000000 for BTC; NULL if unlimited)
    launch_year     INTEGER,           -- year the coin/token launched

    -- ── Metadata ──────────────────────────────────────────────────────────────
    notes           TEXT,              -- free text; manual annotations
    source          TEXT,              -- yfinance | manual | mcp | parsed
    last_updated    TEXT               -- ISO timestamp of last metadata refresh
);
```

### Field Population by Asset Class

| Field | Stock | ETF | Option | Future | Crypto | Source |
|-------|:-----:|:---:|:------:|:------:|:------:|--------|
| name | ✅ | ✅ | ✅ | ✅ | ✅ | Stocks/ETFs: yfinance · Options: parsed · Futures: webull `get_futures_instruments` · Crypto: webull `get_crypto_instruments` |
| exchange | ✅ | ✅ | ✅ | ✅ | ✅ | Stocks/ETFs: yfinance · Futures+Crypto: webull |
| currency | ✅ | ✅ | ✅ | ✅ | ✅ | yfinance (default USD) |
| country | ✅ | ✅ | — | — | — | yfinance |
| is_active | ✅ | ✅ | ✅ | ✅ | ✅ | Crypto: webull status (OC/CO/NT) · Options/Futures: derived from expiry_date · Stocks: yfinance |
| sector | ✅ | — | — | — | — | yfinance |
| industry | ✅ | — | — | — | — | yfinance |
| etf_category | — | ✅ | — | — | — | yfinance fund_category |
| etf_fund_family | — | ✅ | — | — | — | yfinance |
| etf_expense_ratio | — | ✅ | — | — | — | yfinance |
| etf_tracking_index | — | ✅ | — | — | — | yfinance |
| inception_date | ✅ | ✅ | — | — | — | yfinance |
| underlying | — | — | ✅ | ✅ | — | parsed from symbol |
| expiry_date | — | — | ✅ | ✅ | — | Options: parsed from OCC symbol · Futures: schwab/TS MCP |
| strike | — | — | ✅ | — | — | parsed from OCC symbol |
| call_put | — | — | ✅ | — | — | parsed from OCC symbol |
| option_style | — | — | ✅ | — | — | default American; MCP if available |
| contract_size | — | — | ✅ | ✅ | — | Options: 100 (standard) · Futures: schwab/TS MCP |
| futures_root | — | — | — | ✅ | — | parsed from symbol |
| contract_month | — | — | — | ✅ | — | parsed from symbol |
| tick_size | — | — | — | ✅ | — | schwab / TS MCP |
| tick_value | — | — | — | ✅ | — | schwab / TS MCP |
| first_notice_date | — | — | — | ✅ | — | schwab / TS MCP |
| last_trading_date | — | — | — | ✅ | — | schwab / TS MCP |
| blockchain | — | — | — | — | ✅ | yfinance / manual |
| max_supply | — | — | — | — | ✅ | yfinance |
| launch_year | — | — | — | — | ✅ | yfinance / manual |

### What is NOT stored here (changes over time)
- Current price, market cap (exact), beta, dividend yield, IV, shares outstanding, P&L, open interest

### Behaviour

| # | Rule | Detail |
|---|------|--------|
| I.1 | **New symbol detection** | On every ingest/refresh, collect all symbols from incoming positions and transactions. Any symbol not in `instruments` triggers a fetch before positions are written |
| I.2 | **Stock / ETF** | yfinance `ticker.info`: name, sector, industry, exchange, asset_class (Stock vs ETF via `quoteType`), ETF fields (fund_family, expense_ratio, tracking_index, category) |
| I.3 | **Option parsing** | OCC format (`AAPL250516C00200000`) decoded: underlying=AAPL, expiry=2025-05-16, strike=200, call_put=C. No yfinance call needed |
| I.4 | **Futures** | Name + exchange from webull `get_futures_instruments` / `get_futures_products`. Root + contract_month parsed from symbol. Tick size, tick value, contract size, expiry/notice dates from schwab or TS MCP |
| I.5 | **Crypto** | Name + exchange + is_active from webull `get_crypto_instruments` (status: OC=active, CO=liquidate-only, NT=inactive). Blockchain, max_supply, launch_year from yfinance or manual |
| I.6 | **Refresh cadence** | Re-fetched only when `last_updated` > 7 days old or symbol first seen — never on every dashboard load |
| I.7 | **Positions JOIN** | All position queries JOIN `instruments` on symbol — name, sector, asset_class, expiry, strike etc. all served from one table |
| I.8 | **Transactions JOIN** | Transaction rows with a symbol JOIN `instruments` for display enrichment in the Transactions tab |
| I.9 | **Manual override** | `source = 'manual'` rows are never overwritten by auto-fetch |
| I.10 | **is_active flag** | Set to 0 automatically when option/future expiry_date has passed; keeps symbol in DB for historical reference without cluttering active views |

### Impact on yfinance Usage

| Before | After |
|--------|-------|
| yfinance called on every dashboard load for every equity position | Called only for new/stale symbols (>7 days); zero calls for options/futures |
| Sector/name lost on restart | Persisted permanently in DB |
| ETF type detection repeated every load | Stored once in `asset_class` |
| Options had no name/underlying in DB | Fully described by parsed OCC fields |
| Futures tick size required MCP call at display time | Stored once at first ingest |

---

## 7. Ingest / Refresh Architecture

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

## 8. Schema Changes

| Table / Column | Change | When |
|----------------|--------|------|
| `instruments` | **New table** — symbol master catalog (see Section 6 for full schema) | Phase 1 |
| `positions` | Add `asset_type` TEXT (equity/option/future/crypto) | Phase 1 |
| `positions` | Add `underlying`, `expiry`, `strike`, `call_put` TEXT (nullable) | Phase 1 |
| `positions` | Add `data_source` TEXT (mcp/csv) | Phase 1 |
| `positions` | Add `price_source` TEXT (live/yfinance/static) | Phase 1 |
| `positions` | Remove `sector`, `name`, `TYPE` columns — served by JOIN to `instruments` | Phase 1 |
| `transactions` | Add `data_source` TEXT (mcp/csv) | Phase 1 |
| `accounts` | Retire `TRADIER-OPT`, `SCHWAB-OPT` rows | Phase 3 |
| `options_positions` | Deprecate (stop writing; keep for read during transition) | Phase 3 |
| `futures_positions` | Deprecate | Phase 3 |
| `crypto_positions` | Deprecate | Phase 3 |

---

## 9. Out of Scope

| Item | Reason |
|------|--------|
| Real-time streaming prices | MCP tools are request/response only |
| Options Greeks | Out of scope for trading journal; will be addressed in a separate options-focused project |
| Order placement from dashboard | Dashboard is a read-only journal |
| TradeStation history > 89 days | API hard limit; one-time CSV backfill covers the gap |
| Coinbase MCP (balaji-agentkit) | On-chain wallet only; no account transaction history |
| Google Calendar / TradeStation streaming | Not connected / out of scope for journal |
