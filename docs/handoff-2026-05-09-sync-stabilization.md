# Handoff: Sync Stabilization (2026-05-09)

## Scope Completed

This handoff captures the stabilization work around broker sync, health checks,
dashboard consistency, and process cleanup.

Latest commits (newest first):

- `4907d6b` Update Claude prompt guide for stabilized sync workflow
- `2818b61` Document zombie-process cleanup script and execution policy usage
- `599d865` Add one-shot script to kill unneeded Python/Node MCP processes
- `c7998c7` Add zombie-kill script and wire dashboard stop to it
- `fc8305b` Keep CSV sync state current when ingest is skipped
- `83b12bb` Stabilize broker sync flow and balance consistency

---

## What Is Now Working

### 1) Schwab sync is local-first (no Claude file-copy dependency)

- `src/journal_cli.py` sync-all now fetches Schwab MCP payloads locally and
  writes:
  - `data/tmp/schwab_equity.json`
  - `data/tmp/schwab_futures.json`
  - `data/tmp/schwab_summary.json`
- Then it runs Schwab ingest from those files.

### 2) TradeStation discrepancy path fixed

- `src/fetchers/tradestation.py` balance normalization now handles key-variant
  payloads and derives `market_value = equity + margin` when needed.
- TS ingest matches broker values when `data/tmp/ts_balances.json` is fresh.

### 3) TS precheck added before ingest

- `src/journal_cli.py` sync-all now validates:
  - file existence for `ts_positions.json` and `ts_balances.json`
  - JSON parse validity
  - freshness window (hours)
- Clear actionable message is printed if invalid/stale.

### 4) CSV Sync State no longer goes blank when ingest is skipped

- `src/journal_cli.py` now stamps `csv_ingest_state` as
  `skipped_unchanged` when tracked CSV files are unchanged.
- This keeps Health Checks `CSV Sync State` current per run.

### 5) Process cleanup scripts added

- `scripts/kill_zombies.ps1`
  - wired into interactive CLI `Housekeeping -> 7 (Stop dashboard)`
- `scripts/kill_not_needed.ps1`
  - one-shot broader cleanup for unneeded Python/Node MCP/dev processes
  - supports preview and `-Apply`

---

## Canonical Runbook (Current)

1. Run sync from CLI:
   - `python -m src.journal_cli`
   - Choose `9` (Sync all brokers + CSV + snapshot)
2. If TS values are stale:
   - refresh `data/tmp/ts_positions.json` and `data/tmp/ts_balances.json`
   - rerun TS ingest and snapshot
3. Refresh dashboard/API:
   - use CLI housekeeping launch
4. If process/port issues:
   - run cleanup script(s) below

---

## Cleanup Commands

Preview:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\kill_not_needed.ps1
```

Apply:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\kill_not_needed.ps1 -Apply
```

Note: `-ExecutionPolicy Bypass` is used per-command (no machine-wide policy change).

---

## Known Operational Truths

- `/operations/status` must be served by the current API build; if API is old,
  Health Checks sections can appear blank/404 fallback.
- TS accuracy depends on fresh `ts_balances.json` in local `data/tmp`.
- Schwab is now fetched locally during sync-all and no longer depends on
  external copy into `data/tmp`.

---

## Remaining Follow-ups

1. Add explicit CLI menu item: `Refresh account_balances now`.
2. Consider full local TradeStation MCP fetch automation (token/auth permitting).
3. Keep docs synchronized:
   - `docs/USAGE.md`
   - `docs/prompt-guide.md`
   - this handoff file

