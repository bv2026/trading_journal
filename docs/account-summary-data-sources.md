# Account Summary — Column Data Sources

> Legend: **MCP** = broker API call | **CSV** = file import | **CALC** = derived/computed | **yf** = yfinance

---

## Column-by-Column Source Map

| Column | RH-BV / RH-KD | WEBULL | TS | SCHWAB | TRADIER | FIDELITY | COINBASE |
|--------|:-------------:|:------:|:--:|:------:|:-------:|:--------:|:--------:|
| **Market_Value** | trayd `get_positions` → `market_value` | webull `get_account_positions` | TS `get-positions-details` | schwab `get_equity_positions` | Tradier `get_positions` | CSV → yf price × shares | CSV → yf price × qty |
| **Total_Cost** | trayd `get_positions` → `avg_cost × qty` | webull `get_account_positions` | TS `get-positions-details` | schwab `get_equity_positions` | Tradier `get_positions` → `cost_basis` | CSV | CSV |
| **PnL** | CALC: MV − Cost | CALC | CALC | CALC | CALC | CALC | CALC |
| **Return_%** | CALC: PnL ÷ Cost | CALC | CALC | CALC | CALC | CALC | CALC |
| **Margin** | trayd `get_portfolio` → cash (negative = margin used) | webull `get_account_balance` | TS `get-balances-details` | schwab `get_account_summary` | Tradier `get_account_balances` | CSV (Fidelity balance export) | N/A ($0) |
| **Net Cash** | CALC from txns: Σdeposits − Σwithdrawals (CSV only — no RH txn history in MCP) | CALC from txns (webull MCP) | CALC from txns (TS MCP, 89d; CSV backfill) | CALC from txns (schwab MCP) | CALC from txns (Tradier MCP) | CALC from txns (CSV) | CALC from txns (CSV) |
| **Dividends** | CALC from txns (CSV only) | CALC from txns (webull MCP) | CALC from txns (TS MCP, 89d) | CALC from txns (schwab MCP) | CALC from txns (Tradier MCP) | CALC from txns (CSV) | N/A ($0) |
| **Rewards** | CALC from txns (CSV only) | CALC from txns (webull MCP) | CALC from txns (TS MCP, 89d) | CALC from txns (schwab MCP) | CALC from txns (Tradier MCP) | N/A ($0) | CALC from txns (CSV) |
| **Margin Int** | CALC from txns (CSV only) | CALC from txns (webull MCP) | CALC from txns (TS MCP, 89d) | CALC from txns (schwab MCP) | CALC from txns (Tradier MCP) | CALC from txns (CSV) | N/A ($0) |
| **Fees** | CALC from txns (CSV only) | CALC from txns (webull MCP) | CALC from txns (TS MCP, 89d) | CALC from txns (schwab MCP) | CALC from txns (Tradier MCP) | CALC from txns (CSV) | CALC from txns (CSV) |
| **Net Income** | CALC: Div + Rewards − Margin Int − Fees | CALC | CALC | CALC | CALC | CALC | CALC |

---

## Notes

### Market Value & Cost
- All MCP position responses embed both current price and cost basis — no separate price fetch needed
- Fidelity and Coinbase: cost basis from CSV; current price fetched via yfinance at load time
- Schwab options: from schwab MCP; Schwab futures: TOS.csv (static price at ingest)
- Tradier options: merged into TRADIER account via Tradier MCP `get_positions`

### Margin
- Margin balance is an **account-level** field, not derivable from positions
- Must come from broker balance APIs (MCP) or CSV balance exports
- Fidelity: requires a separate balance CSV (currently in `fidelity_Investment_income_balance.csv`)
- Coinbase: no margin — always $0

### Net Cash, Dividends, Rewards, Margin Int, Fees
- All five are **aggregated from the transactions table** (category filter per metric)
- Source of transactions determines freshness:
  - RH: CSV only (no transaction history in trayd MCP) → stale until CSV re-exported
  - TS: MCP covers last 89 days; older history from CSV backfill (one-time)
  - All others: MCP incremental — stay current automatically after initial load

### Net Income
- `Net Income = Dividends + Rewards − Margin Interest − Fees`
- Fully calculated; no direct broker source needed

---

## Gap Analysis — What We Don't Have Yet

| Gap | Affected Accounts | Resolution |
|-----|-------------------|------------|
| RH transaction history | RH-BV, RH-KD | CSV re-export remains required; trayd has no history endpoint |
| TS transactions older than 89 days | TS | One-time CSV backfill on first MCP connect |
| Fidelity margin balance | FIDELITY | Fidelity balance CSV (`fidelity_Investment_income_balance.csv`) — already imported |
| Schwab futures market value | SCHWAB | TOS.csv import (static at ingest time) |
| Coinbase current prices | COINBASE | yfinance by ticker symbol |
