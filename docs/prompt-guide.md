# Prompt Guide: Claude Fetch + CLI Ingest

This guide standardizes the daily sync workflow so Health Checks, CSV state,
and account sync status remain accurate.

## 1) Claude Prompt (fetch only, no Python)

Use this prompt in Claude Desktop:

```text
Fetch fresh live MCP payloads for all brokers and save raw outputs to C:\work\trading-journal\data\tmp.
Do not run Python ingest commands.

WEBULL:
- call mcp__webull__get_account_list
- for each account_id: call mcp__webull__get_account_positions
- for INDIVIDUAL_MARGIN account only: call mcp__webull__get_account_balance
- save:
  - data\tmp\wb_account_list.txt
  - data\tmp\wb_pos_<wb_id>.txt for each account
  - data\tmp\wb_positions_map.rebuilt.json
  - data\tmp\wb_balances_map.json

SCHWAB:
- get_equity_positions -> data\tmp\schwab_equity.json
- get_futures_positions -> data\tmp\schwab_futures.json
- get_account_summary -> data\tmp\schwab_summary.json

TRADIER:
- get_positions -> data\tmp\tradier_pos.json
- get_market_quotes (all symbols from positions) -> data\tmp\tradier_quotes.json
- get_account_balances(accountNumber=6YB44166) -> data\tmp\tradier_balances.json

TRADESTATION:
- get-positions-details(accounts=11908624) -> data\tmp\ts_positions.json
- get-balances-details(accounts=11908624) -> data\tmp\ts_balances.json

ROBINHOOD:
- for RH-BV account_number: get_positions -> data\tmp\rh_pos.json, get_portfolio -> data\tmp\rh_port.json
- for RH-KD account_number: get_positions -> data\tmp\rh_kd_pos.json, get_portfolio -> data\tmp\rh_kd_port.json

Return a final file checklist and any broker errors.
```

## 2) CLI Ingest (single command)

```powershell
cd C:\work\trading-journal
python -m src.journal_cli
```

- Choose `9` (Sync all brokers + CSV + snapshot)
- This will:
  - ingest MCP broker payloads from `data\tmp\`
  - run Coinbase sync
  - run CSV ingest only when tracked files changed
  - optionally prompt for CASH update
  - optionally prompt for Fidelity margin override
  - write final snapshot

## 3) Dashboard Verify

Launch dashboard (from CLI Housekeeping menu) or manually:

```powershell
cd C:\work\trading-journal
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8010
```

In another terminal:

```powershell
cd C:\work\trading-journal\ui
$env:NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:8010"
node .\node_modules\next\dist\bin\next dev -p 3000
```

Then open `http://localhost:3000` and check **Health Checks**:
- `MCP Health`
- `CSV Sync State`
- `Account Sync Status`

## Notes

- If `operations/status` is unavailable, UI falls back and status may be less precise.
- `CSV Sync State` is sourced from DB table `csv_ingest_state`.
- Fidelity freshness depends on:
  - `activity\fidelity_Investment_income_balance.csv`
  - `activity\positions-fidelity.csv`
