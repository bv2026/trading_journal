"""
Ingest broker CSV files into data/journal.db.

Default (incremental):
    python -m src.ingest
    Only new records are added; existing ones are left untouched.
    Drop only the latest CSV export from each broker — no need to re-download
    full history every time.

Full rebuild:
    python -m src.ingest --reset
    Clears all transactions and reloads from every CSV currently in activity/.
    Use this once after first setup or whenever you want a clean slate.

Special cases:
    Fidelity — yearly summary file is always refreshed (current-year figures
    change as the year progresses), regardless of --reset.
"""
import sys
import argparse
import inspect
from datetime import datetime, timezone
from datetime import date as _date
from pathlib import Path
from datetime import timezone

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db
from src.parsers import robinhood, webull, tradestation, schwab, tradier, coinbase, fidelity
from src.parsers import positions_csv
from src.parsers import static_positions_csv

ACTIVITY = Path(__file__).resolve().parents[1] / "activity"

ACCOUNTS = [
    # ── Equity accounts (live yfinance prices) ────────────────────────────────
    {"account_id": "RH-BV",    "broker": "robinhood",    "account_type": "equity",
     "account_group": "investment", "holder": "BV",   "price_source": "live",   "active": 1},
    {"account_id": "RH-KD",    "broker": "robinhood",    "account_type": "equity",
     "account_group": "investment", "holder": "KD",   "price_source": "live",   "active": 1},
    {"account_id": "WEBULL",       "broker": "webull", "account_type": "equity",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "WEBULL-CASH",  "broker": "webull", "account_type": "equity",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "WEBULL-EVENTS","broker": "webull", "account_type": "equity",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "WEBULL-FUT",   "broker": "webull", "account_type": "futures",
     "account_group": "investment", "holder": None, "price_source": "live", "active": 1},
    {"account_id": "TS",       "broker": "tradestation", "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    {"account_id": "SCHWAB",   "broker": "schwab",       "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    {"account_id": "TRADIER",  "broker": "tradier",      "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    {"account_id": "FIDELITY", "broker": "fidelity",     "account_type": "equity",
     "account_group": "investment", "holder": None,   "price_source": "live",   "active": 1},
    # ── Crypto account (transactions only; positions via CRYPTO_FILES) ─────────
    {"account_id": "COINBASE", "broker": "coinbase",     "account_type": "crypto",
     "account_group": "investment", "holder": None,   "price_source": "live", "active": 1},
]

# Accounts whose CSVs are yearly summaries rather than running transaction logs.
# These are always fully refreshed so mid-year updates are picked up.
ALWAYS_REFRESH = {"FIDELITY"}

# Accounts whose current positions are normally sourced from broker MCP syncs.
# Legacy position CSVs for these accounts are skipped by default so opening the
# dashboard or running a transaction ingest cannot overwrite fresher MCP data.
MCP_POSITION_ACCOUNTS = {
    "RH-BV",
    "WEBULL",
    "WEBULL-CASH",
    "WEBULL-EVENTS",
    "WEBULL-FUT",
    "TS",
    "SCHWAB",
    "TRADIER",
    "COINBASE",
}

PARSERS = [
    (robinhood.parse,       ACTIVITY / "robinhood-inv-bv.csv",                          "RH-BV"),
    (robinhood.parse,       ACTIVITY / "robinhood-inv-kd.csv",                          "RH-KD"),
    (webull.parse_inv,      ACTIVITY / "WEBULL-inv.csv",                                "WEBULL"),
    (webull.parse_cash,     ACTIVITY / "WEBULL-cash.csv",                               "WEBULL"),
    (tradestation.parse,    ACTIVITY / "tdstation-cash.csv",                            "TS"),
    (schwab.parse,          ACTIVITY / "schwab.csv",                                    "SCHWAB"),
    (tradier.parse,         ACTIVITY / "tradier.csv",                                   "TRADIER"),
    (coinbase.parse,        ACTIVITY / "coinbase-main.csv",                             "COINBASE"),
    (fidelity.parse,        ACTIVITY / "fidelity_Investment_income_balance.csv",        "FIDELITY"),
]

# Per-account equity positions CSVs — always fully replaced on each ingest.
POSITION_FILES = [
    (ACTIVITY / "positions-scwb.csv",      "SCHWAB"),
    (ACTIVITY / "positions-trader.csv",    "TRADIER"),
    (ACTIVITY / "positions-tradestn.csv",  "TS"),
    (ACTIVITY / "positions-rh-bv.csv",     "RH-BV"),
    (ACTIVITY / "positions-rh-kd.csv",     "RH-KD"),
    (ACTIVITY / "positions-webull.csv",    "WEBULL"),
    (ACTIVITY / "positions-fidelity.csv",  "FIDELITY"),
    (ACTIVITY / "positions-coinbase.csv",  "COINBASE"),
]

# Static options/futures/crypto position CSVs (broker CSV exports).
# Each tuple is (path, account_id). Parsed via static_positions_csv.
OPTIONS_FILES: list[tuple] = []
FUTURES_FILES: list[tuple] = []
CRYPTO_FILES:  list[tuple] = []



def _compute_snapshot_map() -> dict[str, dict]:
    """Build per-account market-value summary for portfolio_snapshots.

    Equity accounts: live prices via yfinance (calls load_positions_from_db).
    Static accounts: market_value stored in DB at ingest time.
    """
    from src.positions import load_positions_from_db  # avoid circular import at module level

    snap: dict[str, dict] = {}

    # ── Equity (live prices) ──────────────────────────────────────────────────
    eq_df = load_positions_from_db()
    if not eq_df.empty and "MARKET VALUE" in eq_df.columns:
        is_margin = eq_df["Ticker"].str.upper() == "MARGIN"
        for acct, grp in eq_df.groupby("Account"):
            grp_margin = is_margin.reindex(grp.index, fill_value=False)
            mv = float(pd.to_numeric(
                grp.loc[~grp_margin, "MARKET VALUE"], errors="coerce"
            ).fillna(0).sum())
            margin = abs(float(pd.to_numeric(
                grp.loc[grp_margin, "MARKET VALUE"], errors="coerce"
            ).fillna(0).sum()))
            cost = float(pd.to_numeric(
                grp.loc[~grp_margin, "COST"], errors="coerce"
            ).fillna(0).sum()) if "COST" in grp.columns else 0.0
            snap[str(acct)] = {"market_value": mv, "cost_basis": cost, "margin": margin}

    # ── Options ───────────────────────────────────────────────────────────────
    opt_df = db.load_options_db()
    if not opt_df.empty:
        for acct, grp in opt_df.groupby("account_id"):
            mv = float(pd.to_numeric(grp["market_value"], errors="coerce").fillna(0).sum())
            snap.setdefault(str(acct), {"market_value": 0.0, "cost_basis": None, "margin": 0.0})
            snap[str(acct)]["market_value"] += mv

    # ── Futures ───────────────────────────────────────────────────────────────
    fut_df = db.load_futures_db()
    if not fut_df.empty:
        for acct, grp in fut_df.groupby("account_id"):
            mv = float(pd.to_numeric(grp["market_value"], errors="coerce").fillna(0).sum())
            snap.setdefault(str(acct), {"market_value": 0.0, "cost_basis": None, "margin": 0.0})
            snap[str(acct)]["market_value"] += mv

    # ── Crypto ────────────────────────────────────────────────────────────────
    cry_df = db.load_crypto_db()
    if not cry_df.empty:
        for acct, grp in cry_df.groupby("account_id"):
            mv = float(pd.to_numeric(grp["market_value"], errors="coerce").fillna(0).sum())
            snap.setdefault(str(acct), {"market_value": 0.0, "cost_basis": None, "margin": 0.0})
            snap[str(acct)]["market_value"] += mv

    # Broker-reported account balances are authoritative when present. They
    # capture cash-only sub-accounts and broker equity that may not be modeled
    # as individual position rows.
    latest_balances = db.load_account_balances()
    if not latest_balances.empty:
        for _, row in latest_balances.iterrows():
            account_id = str(row["account_id"])
            persisted_cost = row.get("cost_basis")
            snap[account_id] = {
                "market_value": float(row.get("market_value") or 0.0),
                "cost_basis": persisted_cost
                if pd.notna(persisted_cost)
                else snap.get(account_id, {}).get("cost_basis"),
                "margin": float(row.get("margin") or 0.0),
            }

    return snap


def run(reset: bool = False, include_mcp_position_csv: bool = False) -> None:
    print("Initializing database …")
    db.init_db()
    db.upsert_accounts(ACCOUNTS)
    db.set_account_price_source("COINBASE", "live")

    if reset:
        print("--reset: clearing all existing transactions …")
        db.clear_transactions()

    total_new = 0
    total_skipped = 0

    for parse_fn, path, acct in PARSERS:
        if not path.exists():
            print(f"  SKIP  {acct}: file not found ({path.name})")
            continue

        try:
            sig = inspect.signature(parse_fn)
            recs = parse_fn(str(path), acct) if len(sig.parameters) >= 2 else parse_fn(str(path))
        except Exception as exc:
            print(f"  ERROR {acct}: {exc}")
            continue

        # Fidelity is a yearly summary whose current-year row changes over time.
        # Always delete and re-insert so updated figures are picked up.
        if acct in ALWAYS_REFRESH and not reset:
            db.delete_by_account(acct)

        # Deduplicate within the parsed batch (e.g. two Webull files for same account)
        seen_in_batch: set[str] = set()
        unique_recs = []
        for r in recs:
            if r["id"] not in seen_in_batch:
                seen_in_batch.add(r["id"])
                unique_recs.append(r)

        inserted = db.insert_transactions(unique_recs)
        skipped  = len(unique_recs) - inserted
        total_new     += inserted
        total_skipped += skipped

        status = f"{inserted:>5} new"
        if skipped:
            status += f"  ({skipped} already in DB)"
        print(f"  OK    {acct}: {status}")
        stat = path.stat()
        db.upsert_csv_ingest_state(
            file_path=str(path.resolve()),
            account_id=acct,
            file_role="transactions",
            file_mtime_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat() if stat else None,
            file_size_bytes=stat.st_size if stat else None,
            rows_written=inserted,
            status="ok",
            detail=f"new={inserted}, skipped={skipped}",
        )

    print(f"\nDone — {total_new} new records added, {total_skipped} already existed.")
    if total_skipped and not reset:
        print("Tip: run with --reset to do a full rebuild from all CSV files.")

    # ── Equity positions (always fully replaced per account) ──────────────────
    pos_total = 0
    for path, acct in POSITION_FILES:
        if acct in MCP_POSITION_ACCOUNTS and not include_mcp_position_csv:
            if path.exists():
                print(f"  SKIP  positions {acct}: MCP-owned account (use --include-mcp-position-csv to override)")
            continue
        if not path.exists():
            continue
        try:
            recs = positions_csv.parse(str(path), acct)
        except Exception as exc:
            print(f"  ERROR positions {acct}: {exc}")
            continue

        db.delete_positions_by_account(acct)
        written = db.insert_positions(recs)
        pos_total += written
        print(f"  OK    positions {acct}: {written} rows")
        stat = path.stat()
        db.upsert_csv_ingest_state(
            file_path=str(path.resolve()),
            account_id=acct,
            file_role="positions",
            file_mtime_utc=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat() if stat else None,
            file_size_bytes=stat.st_size if stat else None,
            rows_written=written,
            status="ok",
            detail=f"rows={written}",
        )

    if pos_total:
        print(f"\nPositions — {pos_total} rows written across accounts.")

    # ── Static options / futures / crypto positions ───────────────────────────
    _static_map = [
        (OPTIONS_FILES, "options", db.delete_options_by_account, db.insert_options),
        (FUTURES_FILES, "futures", db.delete_futures_by_account, db.insert_futures),
        (CRYPTO_FILES,  "crypto",  db.delete_crypto_by_account,  db.insert_crypto),
    ]
    for file_list, asset_type, del_fn, ins_fn in _static_map:
        for path, acct in file_list:
            if not Path(path).exists():
                print(f"  SKIP  {asset_type} {acct}: file not found")
                continue
            try:
                recs = static_positions_csv.parse(str(path), acct, asset_type)
            except Exception as exc:
                print(f"  ERROR {asset_type} {acct}: {exc}")
                continue
            del_fn(acct)
            written = ins_fn(recs)
            print(f"  OK    {asset_type} {acct}: {written} rows")

    # ── Sector/industry enrichment ────────────────────────────────────────────
    print("\nEnriching instrument sectors via yfinance …")
    try:
        from src.enrichment import enrich_sectors
        enriched = enrich_sectors()
        print(f"  Enriched {enriched} instrument(s) with sector/industry data.")
    except Exception as exc:
        print(f"  WARNING sector enrichment failed: {exc}")

    # ── Portfolio snapshot ────────────────────────────────────────────────────
    print("\nWriting portfolio snapshot …")
    try:
        snap_map = _compute_snapshot_map()
        if snap_map:
            today = _date.today().isoformat()
            db.write_portfolio_snapshot(today, snap_map)
            print(f"  Snapshot — {len(snap_map)} accounts written for {today}")
        else:
            print("  Snapshot — no positions found, skipped")
    except Exception as exc:
        print(f"  WARNING snapshot failed: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest broker CSVs into journal.db")
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear all transactions and reload from scratch (use after first setup "
             "or when switching from an older version of this tool).",
    )
    parser.add_argument(
        "--cash", type=float, metavar="AMOUNT",
        help="Set combined cash account balance (Fidelity/PNC/Huntington/Clearview). "
             "e.g. --cash 52400",
    )
    parser.add_argument(
        "--snapshot-only", action="store_true",
        help="Skip CSV parsing; recompute and write today's portfolio snapshot from "
             "current DB positions. Use after an MCP position sync.",
    )
    parser.add_argument(
        "--include-mcp-position-csv", action="store_true",
        help="Allow legacy position CSV files to overwrite MCP-owned accounts. "
             "By default those accounts keep MCP-synced positions.",
    )
    args = parser.parse_args()

    if args.cash is not None:
        from src.db import upsert_cash_balance, init_db
        init_db()
        upsert_cash_balance(args.cash)
        print(f"OK Cash balance set to ${args.cash:,.2f}")
    elif args.snapshot_only:
        db.init_db()
        snap_map = _compute_snapshot_map()
        if snap_map:
            today = _date.today().isoformat()
            db.write_portfolio_snapshot(today, snap_map)
            print(f"Snapshot written for {today} — {len(snap_map)} accounts")
        else:
            print("No positions found, snapshot skipped")
    else:
        run(reset=args.reset, include_mcp_position_csv=args.include_mcp_position_csv)
