# Dashboard Capability Parity

This checklist captures the current Next.js dashboard behavior that must be
preserved as the app is refactored into shared services, CLI receipts, MCP
tools, or a replacement UI.

The machine-readable source of truth is
`src/services/dashboard_capabilities.py`. To inspect it:

```bash
python -m src.cli.main dashboard capabilities
```

## Required Tabs

The current dashboard has eight top-level tabs:

1. Portfolio
2. Yearly Summary
3. By Account
4. Positions
5. Transactions
6. Performance
7. Broker MCP
8. Settings

Any future dashboard must keep equivalent capability coverage for all eight.
The UI may change, but the workflow should not disappear.

## Current Capability Groups

### Global Controls

- Date range filter for transaction-driven views.
- Account filter for transaction-driven views.
- Internal transfer include/exclude toggle.
- Refresh action that clears cached dashboard data.

### Portfolio

- Net worth banner with net worth, market value, and margin borrowed.
- Transaction KPI row for cash flow, dividends, rewards, costs, and net income.
- Account summary with market value, cost basis, margin, net equity, and totals.
- Asset class breakdown for stocks, options, futures, crypto, cash, and total.
- Futures by commodity grouping.
- Sector allocation chart.
- Positions by account expanders, including equity and option details.
- Sector summary with allocation, return, P&L, and lifetime dividends.

### Yearly Summary

- Year-over-year summary for cash flow, income, costs, and net income.
- Income breakdown by dividend/reward subcategory.

### By Account

- Net cash flow by account.
- Dividends plus rewards by account.
- Margin interest plus fees by account.
- Coinbase crypto flow inflow, outflow, and net movement view.

### Positions

- Broker filter.
- Equity sub-tab with aggregated holdings, market value, cost, P&L, return,
  dividends, and footer totals.
- Options sub-tab with contracts grouped by account.
- Futures sub-tab with contracts grouped by account.
- Crypto sub-tab with holdings, cost basis, market value, and P&L.

### Transactions

- Category, broker, year, and description filters.
- Transaction table with date, account, broker, category, subcategory, amount,
  currency, symbol, and description.
- Filtered CSV download.

### Performance

- Portfolio Summary table with current value, 1-week prior value, dollar change,
  and percent change.
- Portfolio Returns table with 1-week, 1-month, 3-month, YTD, and 1-year
  returns.
- Margin-adjusted net value calculation: current market value minus borrowed
  margin.

### Broker MCP

- MCP health check and health table.
- CLI module review.
- Explicit button-triggered Coinbase and Tradier live balance checks.

### Settings

- Cash balance editor.
- Account active and price source editor.
- Margin fallback editor for non-API margin accounts.
- Schwab futures equity override with immediate adjustment-row application.
- Coinbase cost basis adjustment.
- Save All action that persists changes, clears cache, and reruns the dashboard.

## Migration Rule

Before replacing or removing the existing Next.js dashboard, run:

```bash
pytest tests/unit/test_dashboard_capabilities.py tests/unit/test_cli_main.py -q
python -m src.cli.main dashboard capabilities
```

Then verify the replacement UI covers every required capability ID returned by
the CLI command.
